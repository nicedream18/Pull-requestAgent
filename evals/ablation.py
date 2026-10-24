"""Verifier ablation harness: one model run, two verifier configurations.

Rungs B and C must differ only in the verifier, so calling the model twice would mix
model nondeterminism into the comparison. This script calls the model once, freezes
the raw findings, then replays those same findings through both verifier settings and
scores each with the same evaluator and the same ground truth.

Rung A is different and is not computed here: it is the archived prompt-v1 output, and
it changes both the prompt and the output schema. Compare it with `evals.replay_v1`.

Usage:

    # offline: exercises the harness with the deterministic runner, no API key
    PYTHONPATH=src .venv/bin/python -m evals.ablation --mode offline --output artifacts/evals/ablation

    # online: one real model pass, then both verifier configurations
    PYTHONPATH=src .venv/bin/python -m evals.ablation --mode online \
        --model deepseek/deepseek-flash --output artifacts/evals/ablation

    # plan only
    PYTHONPATH=src .venv/bin/python -m evals.ablation --mode online --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from evals.fixtures import DATASET, FixtureClient, OfflineFixtureRunner, snapshot_for
from evals.import_reviews import dataset_review_gate
from evals.metrics import EVALUATOR_VERSION, evaluate_cases
from evals.run import _git_commit, _price_env
from prguard.agents import (
    DEFAULT_PROMPT_VERSION,
    available_prompt_versions,
    prompt_sha,
    prompt_version_or_default,
)
from prguard.flow import ReviewFlow
from prguard.models import Finding, FlowState, PRReviewRequest
from prguard.verifier import verify_findings

HISTORICAL_CASE_COUNT = 15
HISTORICAL_INPUT_TOKENS = 72_197
HISTORICAL_OUTPUT_TOKENS = 20_768
HISTORICAL_LLM_CALLS = 41


def _load_cases(path: Path) -> tuple[dict, list[dict]]:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    return dataset, dataset["cases"]


def _run_once(
    cases: list[dict],
    mode: str,
    model: str | None,
    output_dir: Path,
    prompt_version: str = "",
) -> tuple[list[FlowState], list[dict]]:
    """One strategy pass, keeping the frozen pre-verifier snapshot of each case.

    A case that raises is recorded and the pass continues. Paid model output from the
    cases that already ran is never discarded because a later case failed.
    """
    states: list[FlowState] = []
    failures: list[dict] = []
    for case in cases:
        snapshot, sources = snapshot_for(case)
        runner = OfflineFixtureRunner(sources) if mode == "offline" else None
        request = PRReviewRequest(
            repo=snapshot.repo,
            pr_number=snapshot.pr_number,
            strategy="dynamic",
            mode=mode,
            model=model,
            output_dir=str(output_dir / "reviews" / case["case_id"]),
            max_agent_calls=6,
            concurrency=3,
            prompt_version=prompt_version,
            # The verifier is applied afterwards, once per rung, so this pass must not
            # pre-empt either configuration.
            condition_checks=True,
        )
        flow = ReviewFlow(github_client=FixtureClient(snapshot, sources), agent_runner=runner)
        try:
            flow.kickoff(inputs={"request": request.model_dump()})
        except Exception as exc:
            failures.append({
                "case_id": case["case_id"],
                "error_type": type(exc).__name__,
                "error": "review case failed; exception details omitted",
            })
            print(f"[{case['case_id']}] case failed: {type(exc).__name__}")
            states.append(flow.state)
            continue
        states.append(flow.state)
    return states, failures


def _replay_verifier(cases: list[dict], states: list[FlowState], *, check_conditions: bool) -> tuple[dict, dict]:
    """Apply one verifier configuration to the frozen raw findings."""
    by_case: dict[str, list[Finding]] = {}
    rejections: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for case, state in zip(cases, states, strict=True):
        # `state.raw_findings` was deep-copied before the pipeline's own verification,
        # so both rungs start from byte-identical input.
        raw = [item.model_copy(deep=True) for item in state.raw_findings]
        snapshot, sources = snapshot_for(case)
        verified, rejected = verify_findings(
            raw,
            snapshot,
            sources,
            check_conditions=check_conditions,
        )
        by_case[case["case_id"]] = verified
        rejections[case["case_id"]] = len(rejected)
        for entry in rejected:
            key = str(entry.get("type", "unknown"))
            reasons[key] = reasons.get(key, 0) + 1
    scores = evaluate_cases(cases, by_case)
    return scores, {"total": sum(rejections.values()), "by_type": reasons, "by_case": rejections}


def _raw_row(cases: list[dict], states: list[FlowState]) -> dict:
    by_case = {
        case["case_id"]: [item.model_copy(deep=True) for item in state.raw_findings]
        for case, state in zip(cases, states, strict=True)
    }
    scores = evaluate_cases(cases, by_case)
    return scores


def _summarise(label: str, scores: dict, rejections: dict, states: list[FlowState]) -> dict:
    tokens = sum(state.token_usage["total"] for state in states)
    calls = sum(state.token_usage["calls"] for state in states)
    latencies = sorted(state.execution_time for state in states)
    p95 = latencies[max(0, -(-int(0.95 * len(latencies) * 100) // 100) - 1)] if latencies else 0.0
    return {
        "rung": label,
        "tp": scores["tp"],
        "fp": scores["fp"],
        "fn": scores["fn"],
        "precision": scores["defect_precision"],
        "recall": scores["defect_recall"],
        "f1": scores["f1_score"],
        "strict_precision": scores["strict_precision"],
        "strict_recall": scores["strict_recall"],
        "strict_f1": scores["strict_f1"],
        "advisory_count": scores["advisory_count"],
        "advisory_rate": scores["advisory_rate"],
        "mean_findings_per_pr": scores["mean_findings_per_pr"],
        "false_positives_per_no_defect_pr": scores["false_positives_per_no_defect_pr"],
        "needs_human_review_count": scores["needs_human_review_count"],
        "match_channels": scores.get("match_channels", {}),
        "verifier_rejections": rejections["total"],
        "verifier_rejection_reasons": rejections["by_type"],
        "mean_llm_calls": calls / max(1, len(states)),
        "mean_total_tokens": tokens / max(1, len(states)),
        "mean_latency_seconds": sum(latencies) / max(1, len(latencies)),
        "p95_latency_seconds": p95,
    }


def _raw_path_validity(cases: list[dict], states: list[FlowState]) -> dict:
    """Measure model-reported paths before PathResolver can repair them."""
    rows = []
    valid_total = raw_total = 0
    for case, state in zip(cases, states, strict=True):
        snapshot, _ = snapshot_for(case)
        changed_paths = {item.path for item in snapshot.files}
        findings = state.raw_findings
        valid = sum(1 for finding in findings if finding.file_path in changed_paths)
        valid_total += valid
        raw_total += len(findings)
        rows.append({
            "case_id": case["case_id"],
            "raw_findings": len(findings),
            "valid_reported_paths": valid,
            "valid_path_rate": valid / len(findings) if findings else 0.0,
        })
    return {
        "raw_findings": raw_total,
        "valid_reported_paths": valid_total,
        "rate": valid_total / raw_total if raw_total else 0.0,
        "by_case": rows,
        "zero_findings_rate_rule": "0.0 when the run has no raw findings",
    }


def _failure_summary(cases: list[dict], states: list[FlowState], flow_failures: list[dict]) -> dict:
    failed_agents = []
    timeout_runs = parse_error_runs = api_error_runs = 0
    agent_run_count = 0
    failed_case_ids = {entry["case_id"] for entry in flow_failures}
    for case, state in zip(cases, states, strict=True):
        for run in state.agent_runs:
            agent_run_count += 1
            error_type = run.error_type or ""
            if run.status == "success":
                continue
            failed_case_ids.add(case["case_id"])
            timeout = run.status == "timeout" or error_type == "TimeoutError"
            parse_error = error_type == "FindingParseError"
            api_error = any(token in error_type for token in (
                "API", "RateLimit", "Authentication", "ServiceUnavailable", "InternalServer", "BadRequest"
            ))
            timeout_runs += int(timeout)
            parse_error_runs += int(parse_error)
            api_error_runs += int(api_error)
            failed_agents.append({
                "case_id": case["case_id"],
                "agent": run.agent,
                "status": run.status,
                "error_type": error_type or run.status,
            })
    return {
        "flow_failed_case_count": len(flow_failures),
        "cases_with_any_failure": sorted(failed_case_ids),
        "failed_case_count": len(failed_case_ids),
        "agent_run_count": agent_run_count,
        "failed_agent_run_count": len(failed_agents),
        "agent_failure_rate": len(failed_agents) / agent_run_count if agent_run_count else 0.0,
        "api_error_runs": api_error_runs,
        "timeout_runs": timeout_runs,
        "parse_error_runs": parse_error_runs,
        "failed_agents": failed_agents,
    }


def _print_dry_run(
    mode: str,
    model: str | None,
    cases: list[dict],
    prompt_version: str,
    *,
    split: str | None,
    review_gate: dict,
) -> int:
    input_rate = _price_env("PRGUARD_INPUT_PRICE_PER_1M")
    output_rate = _price_env("PRGUARD_OUTPUT_PRICE_PER_1M")
    calls = HISTORICAL_LLM_CALLS / HISTORICAL_CASE_COUNT * len(cases)
    print("PRGuard ablation dry run")
    print(f"  mode            : {mode}")
    print(f"  prompt version  : {prompt_version}")
    print(f"  prompt sha      : {prompt_sha(prompt_version)}")
    print(f"  git commit      : {_git_commit()}")
    print(f"  evaluator       : {EVALUATOR_VERSION}")
    print(f"  model           : {model or os.getenv('PRGUARD_MODEL') or os.getenv('DEEPSEEK_MODEL') or 'deepseek/deepseek-flash'}")
    print(f"  cases           : {len(cases)}")
    print(f"  split           : {split or 'all'}")
    print(f"  human review    : {'eligible' if review_gate['eligible'] else 'BLOCKED'}")
    if not review_gate["eligible"]:
        print(f"  pending cases   : {len(review_gate['pending_cases'])}")
    print(f"  model calls     : about {calls:.0f} (one pass; both rungs reuse the same raw findings)")
    print("  verifier passes : 2 (condition checks off, then on)")
    if input_rate is not None and output_rate is not None:
        input_tokens = HISTORICAL_INPUT_TOKENS / HISTORICAL_CASE_COUNT * len(cases)
        output_tokens = HISTORICAL_OUTPUT_TOKENS / HISTORICAL_CASE_COUNT * len(cases)
        estimate = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
        print(f"  estimated cost  : USD {estimate:.4f} at the configured price")
    else:
        print("  estimated cost  : set PRGUARD_*_PRICE_PER_1M to compute")
    if mode == "online" and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("PRGUARD_API_KEY")):
        print("  API key         : MISSING")
    elif mode == "online":
        print("  API key         : present")
    print("  nothing executed; remove --dry-run to spend tokens")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one model pass and both verifier rungs.")
    parser.add_argument("--mode", choices=("offline", "online"), default="offline")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--split", choices=("dev", "test", "holdout"), default=None)
    parser.add_argument("--output", type=Path, default=Path("artifacts/evals/ablation"))
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--prompt-version",
        choices=available_prompt_versions(),
        default=DEFAULT_PROMPT_VERSION,
        help="Prompt body sent to the agents. This changes the real text, not just a label.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    dataset, all_cases = _load_cases(args.dataset)
    cases = [case for case in all_cases if args.split is None or case.get("split") == args.split]
    if not cases:
        parser.error(f"dataset has no cases for split {args.split!r}")
    review_gate = dataset_review_gate(dataset)
    chosen_prompt = prompt_version_or_default(args.prompt_version)
    if args.dry_run:
        return _print_dry_run(
            args.mode, args.model, cases, chosen_prompt, split=args.split, review_gate=review_gate
        )

    if not review_gate["eligible"]:
        parser.error(
            "candidate dataset is not eligible for scored evaluation; human-review every case, "
            "including clean cases, and publish a corrected version first"
        )

    if args.mode == "online" and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("PRGUARD_API_KEY")):
        parser.error("online mode requires an LLM API key")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"running one {args.mode} pass over {len(cases)} cases")
    states, failures = _run_once(cases, args.mode, args.model, output_dir, chosen_prompt)

    raw_scores = _raw_row(cases, states)
    b_scores, b_rejections = _replay_verifier(cases, states, check_conditions=False)
    c_scores, c_rejections = _replay_verifier(cases, states, check_conditions=True)

    rows = [
        _summarise("A-raw (no verifier)", raw_scores, {"total": 0, "by_type": {}, "by_case": {}}, states),
        _summarise("B (layer 1)", b_scores, b_rejections, states),
        _summarise("C (layer 1 + 2)", c_scores, c_rejections, states),
    ]

    document = {
        "schema_version": "1.0",
        "evaluator_version": EVALUATOR_VERSION,
        "git_commit": _git_commit(),
        "prompt_version": chosen_prompt,
        "prompt_sha": prompt_sha(chosen_prompt),
        "dataset_version": dataset.get("dataset_version", "unknown"),
        "dataset": str(args.dataset),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "split": args.split or "all",
        "case_count": len(cases),
        "failed_cases": failures,
        "failure_summary": _failure_summary(cases, states, failures),
        "raw_path_validity": _raw_path_validity(cases, states),
        "mode": args.mode,
        "model": args.model or os.getenv("PRGUARD_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek/deepseek-flash",
        "sampling_parameters": {
            "temperature": "provider default; not explicitly set by PRGuard",
            "top_p": "provider default; not explicitly set by PRGuard",
        },
        "context_builder": "build_context_bundle; same implementation for every prompt version",
        "postprocessing": {
            "path_resolver": True,
            "deduplication": True,
            "verifier": True,
            "primary_comparison_rung": "C (layer 1 + layer 2)",
        },
        "review_gate": review_gate,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "design": (
            "one model pass; B and C replay the same frozen raw findings through the verifier "
            "with condition_checks off and on, so the only difference is layer 2"
        ),
        "rungs": rows,
        "raw_findings": {
            case["case_id"]: [item.model_dump(mode="json") for item in state.raw_findings]
            for case, state in zip(cases, states, strict=True)
        },
        "verified_by_rung": {
            "B": {
                entry["case_id"]: entry["findings"]
                for entry in b_scores["details"]
            },
            "C": {
                entry["case_id"]: entry["findings"]
                for entry in c_scores["details"]
            },
        },
    }
    destination = output_dir / "ablation.json"
    destination.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    header = f"{'rung':<20}{'P':>7}{'R':>7}{'F1':>7}{'StrictF1':>10}{'TP':>5}{'FP':>5}{'FN':>5}{'rej':>5}{'adv':>5}"
    print(header)
    for row in rows:
        print(
            f"{row['rung']:<20}{row['precision']:>7.3f}{row['recall']:>7.3f}{row['f1']:>7.3f}"
            f"{row['strict_f1']:>10.3f}{row['tp']:>5}{row['fp']:>5}{row['fn']:>5}"
            f"{row['verifier_rejections']:>5}{row['advisory_count']:>5}"
        )
    print(f"\nwrote {destination}")
    if failures:
        print(f"{len(failures)} case(s) failed and are recorded in failed_cases")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
