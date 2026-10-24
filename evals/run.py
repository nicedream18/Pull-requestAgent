from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from evals.fixtures import DATASET, FixtureClient, OfflineFixtureRunner, case_has_defect, snapshot_for
from evals.import_reviews import dataset_review_gate
from evals.metrics import EVALUATOR_VERSION, aggregate_performance
from prguard.agents import (
    DEFAULT_PROMPT_VERSION,
    available_prompt_versions,
    prompt_sha,
    prompt_version_or_default,
)
from prguard.flow import ReviewFlow
from prguard.models import PRReviewRequest


def _git_commit() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _write_chart(path: Path, summaries: dict[str, dict]) -> None:
    colors = {"single": "#2563eb", "static": "#059669", "dynamic": "#d97706"}
    width, height, base_y = 720, 300, 245
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="#ffffff"/>',
              '<text x="28" y="28" font-family="sans-serif" font-size="17" fill="#111827">PRGuard defect F1 by strategy</text>',
              f'<line x1="90" y1="{base_y}" x2="680" y2="{base_y}" stroke="#9ca3af"/>']
    for step in range(5):
        y = base_y - step * 50
        pieces.append(f'<line x1="90" y1="{y}" x2="680" y2="{y}" stroke="#e5e7eb"/>')
        pieces.append(f'<text x="52" y="{y+4}" font-family="sans-serif" font-size="11" fill="#4b5563">{step/4:.2f}</text>')
    for index, (strategy, summary) in enumerate(sorted(summaries.items())):
        value = float(summary["f1_score"])
        x = 180 + index * 170
        bar_height = value * 200
        y = base_y - bar_height
        color = colors[strategy]
        pieces.append(f'<rect x="{x}" y="{y:.1f}" width="72" height="{bar_height:.1f}" fill="{color}"/>')
        pieces.append(f'<text x="{x+36}" y="{y-8:.1f}" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#111827">{value:.3f}</text>')
        pieces.append(f'<text x="{x+36}" y="{base_y+22}" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#111827">{strategy}</text>')
    pieces.append('</svg>')
    path.write_text("\n".join(pieces) + "\n", encoding="utf-8")


def _run_strategy(
    cases: list[dict],
    strategy: str,
    mode: str,
    output_dir: Path,
    model: str | None,
    condition_checks: bool = True,
    prompt_version: str = "",
) -> tuple[dict, list[dict]]:
    case_results: list[dict] = []
    per_case_runs: list[dict] = []
    for case in cases:
        snapshot, sources = snapshot_for(case)
        runner = OfflineFixtureRunner(sources) if mode == "offline" else None
        request = PRReviewRequest(
            repo=snapshot.repo,
            pr_number=snapshot.pr_number,
            strategy=strategy,
            mode=mode,
            model=model,
            output_dir=str(output_dir / "reviews" / strategy / case["case_id"]),
            max_agent_calls=6,
            concurrency=3,
            condition_checks=condition_checks,
            prompt_version=prompt_version,
        )
        flow = ReviewFlow(github_client=FixtureClient(snapshot, sources), agent_runner=runner)
        flow.kickoff(inputs={"request": request.model_dump()})
        state = flow.state
        findings = state.verified_findings
        rejection_types = {"invalid_path", "invalid_line", "evidence_mismatch", "condition_not_met"}
        rejected = [item for item in state.errors if item.get("type") in rejection_types]
        case_results.append({
            "case_id": case["case_id"],
            "findings": findings,
            "raw_findings": [finding.model_dump(mode="json") for finding in state.raw_findings],
            "rejected": rejected,
            "run_metrics": {
                "execution_seconds": state.execution_time,
                "input_tokens": state.token_usage["input"],
                "output_tokens": state.token_usage["output"],
                "total_tokens": state.token_usage["total"],
                "calls": state.token_usage["calls"],
                "agent_runs": len(state.agent_runs),
                "failed_agents": sum(run.status != "success" for run in state.agent_runs),
                "review_status": state.review_status,
                "full_context_tokens": state.metrics.get("full_context_tokens", 0),
                "optimized_context_tokens": state.metrics.get("input_tokens", 0),
                "context_tokens_saved": state.metrics.get("context_tokens_saved", 0),
                "raw_finding_count": len(state.raw_findings),
            },
        })
        per_case_runs.append({
            "case_id": case["case_id"],
            "status": state.review_status,
            "selected_agents": state.selected_agents,
            "findings": [finding.model_dump(mode="json") for finding in findings],
            "raw_findings": [finding.model_dump(mode="json") for finding in state.raw_findings],
            "rejected": rejected,
            "errors": state.errors,
            **case_results[-1]["run_metrics"],
        })
        print(f"[{mode}/{strategy}] {case['case_id']}: {state.review_status}, {len(findings)} verified findings")
    summary = aggregate_performance(case_results, cases)
    return summary, per_case_runs


# Measured on the 15-fixture dynamic run recorded in docs/benchmark.md. Used only
# for a planning estimate; a real invoice is never claimed from these numbers.
MEASURED_CALLS_PER_PR = 2.73
MEASURED_INPUT_TOKENS_PER_PR = 3079.5
MEASURED_OUTPUT_TOKENS_PER_PR = 2463.9
MEASURED_SECONDS_PER_RUN = 10.96 * 15


def _print_dry_run(args, cases: list[dict], dataset: dict, prompt_version: str) -> int:
    strategies = [args.strategy] if args.strategy in {"single", "static", "dynamic"} else ["single", "static", "dynamic"]
    defect_cases = sum(1 for case in cases if case_has_defect(case))
    review_gate = dataset_review_gate(dataset)
    calls = MEASURED_CALLS_PER_PR * len(cases)
    cost_note = "set PRGUARD_INPUT_PRICE_PER_1M and PRGUARD_OUTPUT_PRICE_PER_1M to compute a real cost"
    input_rate = _price_env("PRGUARD_INPUT_PRICE_PER_1M")
    output_rate = _price_env("PRGUARD_OUTPUT_PRICE_PER_1M")
    if input_rate is not None and output_rate is not None:
        estimate = (
            MEASURED_INPUT_TOKENS_PER_PR * len(cases) * input_rate
            + MEASURED_OUTPUT_TOKENS_PER_PR * len(cases) * output_rate
        ) / 1_000_000
        cost_note = f"estimated USD {estimate:.4f} per run at the configured price"
    print("PRGuard evaluation dry run")
    print(f"  mode              : {args.mode}")
    print(f"  strategies        : {', '.join(strategies)}")
    print(f"  dataset           : {args.dataset} v{dataset.get('dataset_version')}, {len(cases)} cases ({defect_cases} with a labelled defect)")
    print(f"  selected split    : {args.split or 'all'}")
    print(f"  human review gate : {'eligible' if review_gate['eligible'] else 'BLOCKED'}")
    if not review_gate["eligible"]:
        print(f"  pending cases     : {len(review_gate['pending_cases'])}")
    print(f"  evaluator         : {EVALUATOR_VERSION}")
    print(f"  prompt version    : {prompt_version}")
    print(f"  prompt sha        : {prompt_sha(prompt_version)}")
    print(f"  git commit        : {_git_commit()}")
    print(f"  condition checks  : {'on' if args.condition_checks == 'on' else 'off'}")
    print(f"  model             : {args.model or os.getenv('PRGUARD_MODEL') or os.getenv('DEEPSEEK_MODEL') or 'deepseek/deepseek-flash'}")
    credentials = "present" if (os.getenv("DEEPSEEK_API_KEY") or os.getenv("PRGUARD_API_KEY")) else "missing"
    print(f"  API key           : {credentials}")
    print(f"  planned LLM calls : about {calls:.0f} for one strategy, {calls * len(strategies):.0f} for all")
    print(f"  expected wall time: about {MEASURED_SECONDS_PER_RUN * len(strategies) / 60:.1f} min per full sweep, from the measured 10.96 s mean per PR")
    print(f"  cost              : {cost_note}")
    print("  nothing was executed; remove --dry-run to spend tokens")
    return 0


def _price_env(name: str) -> float | None:
    try:
        return max(0.0, float(os.environ[name]))
    except (KeyError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run reproducible PRGuard evaluation fixtures.")
    parser.add_argument("--mode", choices=("offline", "online"), required=True)
    parser.add_argument("--strategy", choices=("single", "static", "dynamic", "all"), default=None)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--split", choices=("dev", "test", "holdout"), default=None)
    parser.add_argument("--output", type=Path, default=Path("artifacts/evals"))
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the configuration and print the plan and cost estimate without calling a model.",
    )
    parser.add_argument(
        "--prompt-version",
        choices=available_prompt_versions(),
        default=DEFAULT_PROMPT_VERSION,
        help="Prompt body sent to the agents; changes the real text, not just a label.",
    )
    parser.add_argument(
        "--condition-checks",
        choices=("on", "off"),
        default="on",
        help="Disable layer-2 AST condition checks to ablate the verifier.",
    )
    args = parser.parse_args(argv)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = [case for case in dataset["cases"] if args.split is None or case.get("split") == args.split]
    if not cases:
        parser.error(f"dataset has no cases for split {args.split!r}")

    chosen_prompt = prompt_version_or_default(args.prompt_version)
    if args.dry_run:
        return _print_dry_run(args, cases, dataset, chosen_prompt)
    review_gate = dataset_review_gate(dataset)
    if not review_gate["eligible"]:
        parser.error(
            "candidate dataset is not eligible for scored evaluation; human-review every case, "
            "including clean cases, and publish a corrected version first"
        )
    if args.mode == "online" and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("PRGUARD_API_KEY")):
        parser.error("online mode requires an LLM API key; use --mode offline for fixture-only evaluation")
    if args.split is None and len(cases) < 15:
        parser.error("dataset must contain at least 15 manually reviewable cases")
    strategies = [args.strategy] if args.strategy in {"single", "static", "dynamic"} else (["single", "static", "dynamic"] if args.mode == "offline" else ["dynamic"])
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output / stamp
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, dict] = {}
    all_cases: dict[str, list[dict]] = {}
    for strategy in strategies:
        summary, detail = _run_strategy(
            cases,
            strategy,
            args.mode,
            output_dir,
            args.model,
            condition_checks=args.condition_checks == "on",
            prompt_version=chosen_prompt,
        )
        summaries[strategy] = summary
        all_cases[strategy] = detail

    document = {
        "schema_version": "1.1",
        "evaluator_version": EVALUATOR_VERSION,
        "git_commit": _git_commit(),
        "mode": args.mode,
        "evaluation_type": "deterministic_offline_fixture_runner" if args.mode == "offline" else "live_llm_on_local_fixtures",
        "dataset": str(args.dataset),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "dataset_version": dataset.get("dataset_version", "unknown"),
        "split": args.split or "all",
        "case_count": len(cases),
        "prompt_version": chosen_prompt,
        "prompt_sha": prompt_sha(chosen_prompt),
        "model": args.model or (os.getenv("PRGUARD_MODEL") or os.getenv("DEEPSEEK_MODEL") if args.mode == "online" else "deterministic-fixture-runner"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "condition_checks": args.condition_checks == "on",
        "matching": (
            "unified: defect only, same file, concrete start_line within two lines, evidence present verbatim in "
            "the changed file, then issue_type equality (structured) or label category plus token overlap >= 0.20 "
            "(text). legacy_* keys reproduce the prguard-review-v1 matcher."
        ),
        "strict_rule": "advisories are never true positives and count as false positives in strict_*",
        "zero_denominator_rule": "precision and recall are 0.0 when their denominator is zero",
        "strategies": summaries,
        "cases": all_cases,
    }
    (output_dir / "results.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["mode", "strategy", "evaluator_version", "precision", "recall", "f1", "strict_precision", "strict_recall", "strict_f1", "legacy_precision", "legacy_recall", "legacy_f1", "pre_verifier_precision", "pre_verifier_recall", "pre_verifier_fp", "post_verifier_precision", "post_verifier_recall", "tp", "fp", "fn", "advisory_rate", "mean_findings_per_pr", "false_positives_per_no_defect_pr", "mean_full_context_tokens", "mean_optimized_context_tokens", "mean_context_tokens_saved", "mean_input_tokens", "mean_output_tokens", "mean_total_tokens", "mean_llm_calls", "mean_latency_seconds", "p95_latency_seconds", "estimated_api_cost_usd", "agent_failure_rate", "verifier_rejection_rate"])
        writer.writeheader()
        for strategy, result in summaries.items():
            writer.writerow({
                "mode": args.mode,
                "strategy": strategy,
                "evaluator_version": EVALUATOR_VERSION,
                "precision": result["defect_precision"],
                "recall": result["defect_recall"],
                "f1": result["f1_score"],
                "strict_precision": result["strict_precision"],
                "strict_recall": result["strict_recall"],
                "strict_f1": result["strict_f1"],
                "legacy_precision": result["legacy_precision"],
                "legacy_recall": result["legacy_recall"],
                "legacy_f1": result["legacy_f1"],
                "pre_verifier_precision": result.get("pre_verifier_precision"),
                "pre_verifier_recall": result.get("pre_verifier_recall"),
                "pre_verifier_fp": result.get("pre_verifier_fp"),
                "post_verifier_precision": result.get("post_verifier_precision"),
                "post_verifier_recall": result.get("post_verifier_recall"),
                "tp": result["tp"], "fp": result["fp"], "fn": result["fn"],
                "advisory_rate": result["advisory_rate"],
                "mean_findings_per_pr": result["mean_findings_per_pr"],
                "false_positives_per_no_defect_pr": result["false_positives_per_no_defect_pr"],
                "verifier_rejection_rate": result["verifier_rejection_rate"],
                **{key: result[key] for key in ("mean_full_context_tokens", "mean_optimized_context_tokens", "mean_context_tokens_saved", "mean_input_tokens", "mean_output_tokens", "mean_total_tokens", "mean_llm_calls", "mean_latency_seconds", "p95_latency_seconds", "estimated_api_cost_usd", "agent_failure_rate")},
            })
    chart_name = "benchmark.svg"
    _write_chart(output_dir / chart_name, summaries)
    lines = [
        "# PRGuard Benchmark",
        "",
        f"- Mode: `{args.mode}` ({document['evaluation_type']})",
        f"- Dataset: `{args.dataset}` v{document['dataset_version']}, {len(cases)} development regression fixtures (not an independent human-adjudicated benchmark)",
        f"- Split: `{args.split or 'all'}`",
        f"- Model: `{document['model']}`",
        f"- Prompt: `{chosen_prompt}` (sha `{prompt_sha(chosen_prompt)}`)",
        f"- Evaluated: `{document['evaluated_at']}`",
        f"- Evaluator: `{EVALUATOR_VERSION}`",
        f"- Condition checks: `{'on' if args.condition_checks == 'on' else 'off'}`",
        f"- Git commit: `{_git_commit()}`",
        "- Matching: unified strict rule; `legacy_*` columns reproduce prguard-review-v1.",
        "- Zero denominators produce 0.0 precision or recall.",
        "",
    ]
    if args.mode == "offline":
        lines.extend(["> Offline scores exercise a deterministic regex fixture runner and the routing, verification, and scoring pipeline. They are not LLM quality results and must not be compared with online agent quality.", ""])
    lines.extend([
        "| Strategy | Precision | Recall | F1 | Strict F1 | Pre-verifier R | Post-verifier R | TP | FP | FN | Advisory rate | Findings/PR | FP per clean PR | Input tok/PR | Calls/PR | Mean s | P95 s | Est. USD | Agent fail rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for strategy, result in summaries.items():
        cost = "n/a" if result["estimated_api_cost_usd"] is None else f"{result['estimated_api_cost_usd']:.6f}"
        lines.append(
            f"| {strategy} | {result['defect_precision']:.3f} | {result['defect_recall']:.3f} | {result['f1_score']:.3f} | "
            f"{result['strict_f1']:.3f} | {result.get('pre_verifier_recall', 0):.3f} | {result.get('post_verifier_recall', 0):.3f} | "
            f"{result['tp']} | {result['fp']} | {result['fn']} | {result['advisory_rate']:.3f} | "
            f"{result['mean_findings_per_pr']:.2f} | {result['false_positives_per_no_defect_pr']:.2f} | "
            f"{result['mean_input_tokens']:.1f} | {result['mean_llm_calls']:.2f} | "
            f"{result['mean_latency_seconds']:.3f} | {result['p95_latency_seconds']:.3f} | {cost} | "
            f"{result['agent_failure_rate']:.3f} |"
        )
    lines.extend(["", f"Chart: [{chart_name}]({chart_name})", "", "Per-case outcomes and findings are recorded in `results.json`; the normalized summary is in `results.csv`."])
    (output_dir / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote benchmark outputs to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
