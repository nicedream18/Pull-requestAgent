from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from prguard.models import Finding

STOP_WORDS = {"the", "a", "an", "is", "are", "may", "can", "that", "to", "of", "and", "in", "for", "with", "this", "it", "be", "not", "as", "by"}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 2 and token not in STOP_WORDS}


def _semantic_score(finding: Finding, truth: dict) -> float:
    predicted = _tokens(f"{finding.category} {finding.title} {finding.description}")
    expected = _tokens(f"{truth['defect_type']} {truth['description']}")
    return len(predicted & expected) / max(1, len(predicted | expected))


def _match_score(finding: Finding, truth: dict) -> float | None:
    if finding.file_path != truth["file_path"] or finding.category != truth["category"]:
        return None
    line = finding.start_line
    start, end = truth["line_range"]
    if line is None or line < start - 2 or line > end + 2:
        return None
    semantic = _semantic_score(finding, truth)
    if semantic < 0.045:
        return None
    line_score = 1.0 if start <= line <= end else 0.5
    return 0.45 + 0.25 * line_score + 0.30 * semantic


def evaluate_cases(cases: list[dict], predicted_by_case: dict[str, list[Finding]]) -> dict:
    true_positive = false_positive = false_negative = 0
    per_category: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0})
    no_defect_findings = 0
    details = []
    for case in cases:
        predicted = predicted_by_case.get(case["case_id"], [])
        truths = [case] if case["is_real_defect"] else []
        options = []
        for prediction_index, finding in enumerate(predicted):
            for truth_index, truth in enumerate(truths):
                score = _match_score(finding, truth)
                if score is not None:
                    options.append((score, prediction_index, truth_index))
        used_predictions: set[int] = set()
        used_truths: set[int] = set()
        for _, prediction_index, truth_index in sorted(options, reverse=True):
            if prediction_index in used_predictions or truth_index in used_truths:
                continue
            used_predictions.add(prediction_index)
            used_truths.add(truth_index)
        tp = len(used_predictions)
        fp = len(predicted) - tp
        fn = len(truths) - len(used_truths)
        true_positive += tp
        false_positive += fp
        false_negative += fn
        if case["is_real_defect"]:
            metric = per_category[case["defect_type"]]
            metric["tp"] += tp
            metric["fn"] += fn
        else:
            no_defect_findings += len(predicted)
        details.append({
            "case_id": case["case_id"],
            "is_real_defect": case["is_real_defect"],
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "findings": [item.model_dump(mode="json") for item in predicted],
        })
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    category_recall = {
        name: values["tp"] / (values["tp"] + values["fn"]) if values["tp"] + values["fn"] else 0.0
        for name, values in sorted(per_category.items())
    }
    return {
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
        "defect_precision": precision,
        "defect_recall": recall,
        "f1_score": f1,
        "category_recall": category_recall,
        "false_positives_per_no_defect_pr": no_defect_findings / max(1, sum(not case["is_real_defect"] for case in cases)),
        "details": details,
    }


def aggregate_performance(results: list[dict], cases: list[dict]) -> dict:
    metrics = [item["run_metrics"] for item in results]
    latencies = sorted(float(item.get("execution_seconds", 0)) for item in metrics)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1) if latencies else 0
    count = max(1, len(metrics))
    all_predictions = [finding for item in results for finding in item["findings"]]
    merged = evaluate_cases(cases, {item["case_id"]: item["findings"] for item in results})
    input_tokens = sum(int(item.get("input_tokens", 0)) for item in metrics)
    output_tokens = sum(int(item.get("output_tokens", 0)) for item in metrics)
    total_tokens = sum(int(item.get("total_tokens", 0)) for item in metrics)
    full_context_tokens = sum(int(item.get("full_context_tokens", 0)) for item in metrics)
    optimized_context_tokens = sum(int(item.get("optimized_context_tokens", 0)) for item in metrics)
    context_tokens_saved = sum(int(item.get("context_tokens_saved", 0)) for item in metrics)
    calls = sum(int(item.get("calls", 0)) for item in metrics)
    failures = sum(int(item.get("failed_agents", 0)) for item in metrics)
    agent_runs = sum(int(item.get("agent_runs", 0)) for item in metrics)
    input_rate = _price("PRGUARD_INPUT_PRICE_PER_1M")
    output_rate = _price("PRGUARD_OUTPUT_PRICE_PER_1M")
    estimated_cost = (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000 if input_rate is not None and output_rate is not None else None
    return {
        **{key: value for key, value in merged.items() if key != "details"},
        "mean_input_tokens": input_tokens / count,
        "mean_total_tokens": total_tokens / count,
        "mean_full_context_tokens": full_context_tokens / count,
        "mean_optimized_context_tokens": optimized_context_tokens / count,
        "mean_context_tokens_saved": context_tokens_saved / count,
        "mean_llm_calls": calls / count,
        "mean_latency_seconds": sum(latencies) / count if latencies else 0,
        "p95_latency_seconds": latencies[p95_index] if latencies else 0,
        "estimated_api_cost_usd": estimated_cost,
        "agent_failure_rate": failures / agent_runs if agent_runs else 0,
        "runs": len(results),
        "finding_count": len(all_predictions),
    }


def _price(name: str) -> float | None:
    import os
    try:
        return max(0.0, float(os.environ[name]))
    except (KeyError, ValueError):
        return None
