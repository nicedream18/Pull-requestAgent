from __future__ import annotations

import math
import os
import re
from collections import defaultdict

from prguard.models import Finding, FindingType

STOP_WORDS = {"the", "a", "an", "is", "are", "may", "can", "that", "to", "of", "and", "in", "for", "with", "this", "it", "be", "not", "as", "by"}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 2 and token not in STOP_WORDS}


def _semantic_score(finding: Finding, truth: dict) -> float:
    predicted = _tokens(f"{finding.category} {finding.title} {finding.description}")
    expected = _tokens(f"{truth['defect_type']} {truth['description']}")
    return len(predicted & expected) / max(1, len(predicted | expected))


def _match_score(finding: Finding, truth: dict) -> float | None:
    """Score a prediction against one ground-truth defect.

    Two channels are accepted:

    * structured: finding.issue_type equals the labeled defect_type. This is
      language independent, which matters because agents answer in Chinese while
      the labels are English.
    * text: category equality plus token overlap, retained for legacy findings
      that only carry a free-text description.

    Both channels still require the same file and a line within two lines of the
    labeled range. A finding without a line can only match structurally.
    """
    if finding.file_path != truth["file_path"]:
        return None

    start, end = truth["line_range"]
    line = finding.start_line
    if line is not None and (line < start - 2 or line > end + 2):
        return None

    structured = finding.issue_type.value == truth["defect_type"]
    if structured:
        in_range = line is not None and start <= line <= end
        return 0.90 + (0.10 if in_range else 0.05)

    if finding.category != truth["category"]:
        return None
    if line is None:
        # Without a location the only acceptable evidence is a text match.
        semantic = _semantic_score(finding, truth)
        return 0.5 + 0.5 * semantic if semantic >= 0.20 else None
    semantic = _semantic_score(finding, truth)
    if semantic < 0.045:
        return None
    line_score = 1.0 if start <= line <= end else 0.5
    return 0.45 + 0.25 * line_score + 0.30 * semantic


def _split(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    defects = [item for item in findings if item.finding_type is FindingType.DEFECT]
    advisories = [item for item in findings if item.finding_type is not FindingType.DEFECT]
    return defects, advisories


def _score(cases: list[dict], predicted_by_case: dict[str, list[Finding]], *, include_advisories: bool) -> dict:
    true_positive = false_positive = false_negative = 0
    per_category: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0})
    no_defect_findings = 0
    for case in cases:
        predicted = predicted_by_case.get(case["case_id"], [])
        defects, advisories = _split(predicted)
        counted = defects + advisories if include_advisories else defects
        truths = [case] if case["is_real_defect"] else []
        options = []
        for prediction_index, finding in enumerate(counted):
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
        fp = len(counted) - tp
        fn = len(truths) - len(used_truths)
        true_positive += tp
        false_positive += fp
        false_negative += fn
        if case["is_real_defect"]:
            metric = per_category[case["defect_type"]]
            metric["tp"] += tp
            metric["fn"] += fn
        else:
            no_defect_findings += len(counted)

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "tp": true_positive,
        "fp": false_positive,
        "fn": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "category_recall": {
            name: values["tp"] / (values["tp"] + values["fn"]) if values["tp"] + values["fn"] else 0.0
            for name, values in sorted(per_category.items())
        },
        "false_positives_per_no_defect_pr": no_defect_findings / max(1, sum(not case["is_real_defect"] for case in cases)),
    }


def evaluate_cases(cases: list[dict], predicted_by_case: dict[str, list[Finding]]) -> dict:
    """Score confirmed defects, keeping advisories out of the precision denominator.

    ``strict_*`` counts advisories as false positives so a run cannot improve its
    score by relabelling every finding as advice.
    """
    defect = _score(cases, predicted_by_case, include_advisories=False)
    strict = _score(cases, predicted_by_case, include_advisories=True)
    details = []
    advisory_total = 0
    review_total = 0
    finding_total = 0
    for case in cases:
        predicted = predicted_by_case.get(case["case_id"], [])
        defects, advisories = _split(predicted)
        advisory_total += len(advisories)
        finding_total += len(predicted)
        review_total += sum(1 for item in predicted if item.needs_human_review)
        details.append({
            "case_id": case["case_id"],
            "is_real_defect": case["is_real_defect"],
            "defect_count": len(defects),
            "advisory_count": len(advisories),
            "findings": [item.model_dump(mode="json") for item in predicted],
        })
    return {
        "tp": defect["tp"],
        "fp": defect["fp"],
        "fn": defect["fn"],
        "defect_precision": defect["precision"],
        "defect_recall": defect["recall"],
        "f1_score": defect["f1"],
        "category_recall": defect["category_recall"],
        "false_positives_per_no_defect_pr": defect["false_positives_per_no_defect_pr"],
        "strict_precision": strict["precision"],
        "strict_recall": strict["recall"],
        "strict_f1": strict["f1"],
        "advisory_count": advisory_total,
        "advisory_rate": advisory_total / finding_total if finding_total else 0.0,
        "needs_human_review_count": review_total,
        "mean_findings_per_pr": finding_total / max(1, len(cases)),
        "details": details,
    }


def _agent_stats(results: list[dict], cases: list[dict]) -> dict[str, dict[str, float]]:
    """Per-agent accepted ratio and rejection rate."""
    truth_by_case = {case["case_id"]: case for case in cases}
    accepted: dict[str, int] = defaultdict(int)
    rejected: dict[str, int] = defaultdict(int)
    valid: dict[str, int] = defaultdict(int)
    for item in results:
        case = truth_by_case.get(item["case_id"], {})
        for finding in item["findings"]:
            for agent in str(finding.source_agent).split(", "):
                if not agent:
                    continue
                accepted[agent] += 1
                if case.get("is_real_defect") and _match_score(finding, case) is not None:
                    valid[agent] += 1
        for error in item.get("rejected", []):
            agent = str(error.get("source_agent", "")) or "unknown"
            rejected[agent] += 1
    agents = set(accepted) | set(rejected)
    return {
        agent: {
            "accepted": accepted[agent],
            "rejected": rejected[agent],
            "rejection_rate": rejected[agent] / max(1, accepted[agent] + rejected[agent]),
            "valid_ratio": valid[agent] / max(1, accepted[agent]),
        }
        for agent in sorted(agents)
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
    raw_findings = sum(int(item.get("raw_finding_count", len(item["findings"]))) for item in results)
    rejections = sum(len(item.get("rejected", [])) for item in results)
    rejection_types: dict[str, int] = defaultdict(int)
    for item in results:
        for error in item.get("rejected", []):
            rejection_types[str(error.get("type", "unknown"))] += 1
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
        "raw_finding_count": raw_findings,
        "verified_finding_count": len(all_predictions),
        "verifier_rejection_rate": rejections / max(1, raw_findings),
        "rejection_types": dict(sorted(rejection_types.items())),
        "agent_stats": _agent_stats(results, cases),
        "runs": len(results),
        "finding_count": len(all_predictions),
    }


def _price(name: str) -> float | None:
    try:
        return max(0.0, float(os.environ[name]))
    except (KeyError, ValueError):
        return None
