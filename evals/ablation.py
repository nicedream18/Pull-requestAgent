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
        --model deepseek/deepseek-chat --output artifacts/evals/ablation

    # plan only
    PYTHONPATH=src .venv/bin/python -m evals.ablation --mode online --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from evals.fixtures import DATASET, FixtureClient, OfflineFixtureRunner, snapshot_for
from evals.metrics import EVALUATOR_VERSION, evaluate_cases
from evals.run import _git_commit, _price_env
from prguard.agents import PROMPT_VERSION
from prguard.flow import ReviewFlow
from prguard.models import Finding, FlowState, PRReviewRequest
from prguard.verifier import verify_findings


def _load_cases(path: Path) -> tuple[dict, list[dict]]:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    return dataset, dataset["cases"]


def _run_once(cases: list[dict], mode: str, model: str | None, output_dir: Path) -> list[FlowState]:
    """One strategy pass, keeping the frozen pre-verifier snapshot of each case."""
    states: list[FlowState] = []
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
            # The verifier is applied afterwards, once per rung, so this pass must not
            # pre-empt either configuration.
            condition_checks=True,
        )
        flow = ReviewFlow(github_client=FixtureClient(snapshot, sources), agent_runner=runner)
        flow.kickoff(inputs={"request": request.model_dump()})
        states.append(flow.state)
    return states


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


def _print_dry_run(mode: str, model: str | None, cases: list[dict]) -> int:
    input_rate = _price_env("PRGUARD_INPUT_PRICE_PER_1M")
    output_rate = _price_env("PRGUARD_OUTPUT_PRICE_PER_1M")
    calls = 2.73 * len(cases)
    print("PRGuard ablation dry run")
    print(f"  mode            : {mode}")
    print(f"  prompt          : {PROMPT_VERSION}")
    print(f"  evaluator       : {EVALUATOR_VERSION}")
    print(f"  model           : {model or os.getenv('PRGUARD_MODEL') or os.getenv('DEEPSEEK_MODEL') or 'deepseek/deepseek-chat'}")
    print(f"  cases           : {len(cases)}")
    print(f"  model calls     : about {calls:.0f} (one pass; both rungs reuse the same raw findings)")
    print("  verifier passes : 2 (condition checks off, then on)")
    if input_rate is not None and output_rate is not None:
        estimate = (3079.5 * len(cases) * input_rate + 2463.9 * len(cases) * output_rate) / 1_000_000
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
    parser.add_argument("--output", type=Path, default=Path("artifacts/evals/ablation"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    dataset, cases = _load_cases(args.dataset)
    if args.dry_run:
        return _print_dry_run(args.mode, args.model, cases)

    if args.mode == "online" and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("PRGUARD_API_KEY")):
        parser.error("online mode requires an LLM API key")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"running one {args.mode} pass over {len(cases)} cases")
    states = _run_once(cases, args.mode, args.model, output_dir)

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
        "prompt_version": PROMPT_VERSION,
        "dataset_version": dataset.get("dataset_version", "unknown"),
        "case_count": len(cases),
        "mode": args.mode,
        "model": args.model or os.getenv("PRGUARD_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek/deepseek-chat",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
