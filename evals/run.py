from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from evals.fixtures import DATASET, FixtureClient, OfflineFixtureRunner, snapshot_for
from evals.metrics import aggregate_performance
from prguard.agents import PROMPT_VERSION
from prguard.flow import ReviewFlow
from prguard.models import PRReviewRequest


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
                "raw_finding_count": len(state.findings),
            },
        })
        per_case_runs.append({
            "case_id": case["case_id"],
            "status": state.review_status,
            "selected_agents": state.selected_agents,
            "findings": [finding.model_dump(mode="json") for finding in findings],
            "rejected": rejected,
            "errors": state.errors,
            **case_results[-1]["run_metrics"],
        })
        print(f"[{mode}/{strategy}] {case['case_id']}: {state.review_status}, {len(findings)} verified findings")
    summary = aggregate_performance(case_results, cases)
    return summary, per_case_runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run reproducible PRGuard evaluation fixtures.")
    parser.add_argument("--mode", choices=("offline", "online"), required=True)
    parser.add_argument("--strategy", choices=("single", "static", "dynamic", "all"), default=None)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=Path("artifacts/evals"))
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--condition-checks",
        choices=("on", "off"),
        default="on",
        help="Disable layer-2 AST condition checks to ablate the verifier.",
    )
    args = parser.parse_args(argv)

    if args.mode == "online" and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("PRGUARD_API_KEY")):
        parser.error("online mode requires an LLM API key; use --mode offline for fixture-only evaluation")

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    if len(cases) < 15:
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
        )
        summaries[strategy] = summary
        all_cases[strategy] = detail

    document = {
        "schema_version": "1.0",
        "mode": args.mode,
        "evaluation_type": "deterministic_offline_fixture_runner" if args.mode == "offline" else "live_llm_on_local_fixtures",
        "dataset": str(args.dataset),
        "dataset_version": dataset.get("dataset_version", "unknown"),
        "case_count": len(cases),
        "prompt_version": PROMPT_VERSION,
        "model": args.model or (os.getenv("PRGUARD_MODEL") or os.getenv("DEEPSEEK_MODEL") if args.mode == "online" else "deterministic-fixture-runner"),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "matching": "one-to-one assignment; requires same file and category, line within two lines, and semantic token overlap >= 0.045",
        "zero_denominator_rule": "precision and recall are 0.0 when their denominator is zero",
        "strategies": summaries,
        "cases": all_cases,
    }
    (output_dir / "results.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["mode", "strategy", "precision", "recall", "f1", "tp", "fp", "fn", "mean_full_context_tokens", "mean_optimized_context_tokens", "mean_context_tokens_saved", "mean_input_tokens", "mean_total_tokens", "mean_llm_calls", "mean_latency_seconds", "p95_latency_seconds", "estimated_api_cost_usd", "agent_failure_rate"])
        writer.writeheader()
        for strategy, result in summaries.items():
            writer.writerow({
                "mode": args.mode,
                "strategy": strategy,
                "precision": result["defect_precision"],
                "recall": result["defect_recall"],
                "f1": result["f1_score"],
                "tp": result["tp"], "fp": result["fp"], "fn": result["fn"],
                **{key: result[key] for key in ("mean_full_context_tokens", "mean_optimized_context_tokens", "mean_context_tokens_saved", "mean_input_tokens", "mean_total_tokens", "mean_llm_calls", "mean_latency_seconds", "p95_latency_seconds", "estimated_api_cost_usd", "agent_failure_rate")},
            })
    chart_name = "benchmark.svg"
    _write_chart(output_dir / chart_name, summaries)
    lines = [
        "# PRGuard Benchmark",
        "",
        f"- Mode: `{args.mode}` ({document['evaluation_type']})",
        f"- Dataset: `{args.dataset}` v{document['dataset_version']}, {len(cases)} manually reviewed fixtures",
        f"- Model: `{document['model']}`",
        f"- Prompt: `{PROMPT_VERSION}`",
        f"- Evaluated: `{document['evaluated_at']}`",
        "- Matching: one-to-one, same file/category, line distance <= 2, semantic overlap >= 0.045.",
        "- Zero denominators produce 0.0 precision or recall.",
        "",
    ]
    if args.mode == "offline":
        lines.extend(["> Offline scores exercise a deterministic regex fixture runner and the routing, verification, and scoring pipeline. They are not LLM quality results and must not be compared with online agent quality.", ""])
    lines.extend([
        "| Strategy | Precision | Recall | F1 | TP | FP | FN | Full ctx tok/PR | Opt ctx tok/PR | Input tok/PR | Calls/PR | Mean s | P95 s | Est. USD | Agent fail rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for strategy, result in summaries.items():
        cost = "n/a" if result["estimated_api_cost_usd"] is None else f"{result['estimated_api_cost_usd']:.6f}"
        lines.append(f"| {strategy} | {result['defect_precision']:.3f} | {result['defect_recall']:.3f} | {result['f1_score']:.3f} | {result['tp']} | {result['fp']} | {result['fn']} | {result['mean_full_context_tokens']:.1f} | {result['mean_optimized_context_tokens']:.1f} | {result['mean_input_tokens']:.1f} | {result['mean_llm_calls']:.2f} | {result['mean_latency_seconds']:.3f} | {result['p95_latency_seconds']:.3f} | {cost} | {result['agent_failure_rate']:.3f} |")
    lines.extend(["", f"Chart: [{chart_name}]({chart_name})", "", "Per-case outcomes and findings are recorded in `results.json`; the normalized summary is in `results.csv`."])
    (output_dir / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote benchmark outputs to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
