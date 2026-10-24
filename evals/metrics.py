"""Evaluation metrics for PRGuard.

Two scorers share this module:

* **legacy** reproduces the numbers published for prompt ``prguard-review-v1``. It
  accepts a match on file + category + line window + token overlap, and it lets a
  finding without ``start_line`` match structurally. Do not change it; it exists so
  historical records stay reproducible.
* **unified** is the scorer used for every A/B/C comparison. It is deliberately
  stricter, and it refuses to credit anything it cannot locate and evidence.

``EVALUATOR_VERSION`` must be recorded with every benchmark result.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict

from prguard.models import Finding, FindingType

# Cases may carry one labelled defect (v1 schema) or several (v2 schema). Both are
# read through these helpers so the scoring code has a single shape to reason about.


def case_defects(case: dict) -> list[dict]:
    if "defects" in case:
        return list(case["defects"])
    return [case] if case.get("is_real_defect") else []


def case_sources(case: dict) -> dict[str, str]:
    """File contents for a case, whichever schema it uses.

    A synthetic case may carry neither `files` nor `code`; that is fine, the evidence
    check simply cannot run and the matcher falls back to its other signals.
    """
    if "files" in case:
        return {entry["path"]: entry["code"] for entry in case["files"]}
    if "file_path" in case and "code" in case:
        return {case["file_path"]: case["code"]}
    return {}


def case_has_defect(case: dict) -> bool:
    if "has_defect" in case:
        return bool(case["has_defect"])
    return bool(case.get("is_real_defect"))

EVALUATOR_VERSION = "prguard-eval-v2"

# A located finding may be off by this many lines from the labelled range. The
# fixtures label a single line or a tight block, and reviewers routinely cite the
# statement above or below it.
LINE_SLACK = 2

STOP_WORDS = {"the", "a", "an", "is", "are", "may", "can", "that", "to", "of", "and", "in", "for", "with", "this", "it", "be", "not", "as", "by"}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) > 2 and token not in STOP_WORDS}


def _semantic_score(finding: Finding, truth: dict) -> float:
    predicted = _tokens(f"{finding.category} {finding.title} {finding.description}")
    expected = _tokens(f"{truth['defect_type']} {truth['description']}")
    return len(predicted & expected) / max(1, len(predicted | expected))


# --------------------------------------------------------------------------- legacy


def _legacy_match_score(finding: Finding, truth: dict) -> float | None:
    """Frozen copy of the prguard-review-v1 matcher. Do not edit."""
    if finding.file_path != truth["file_path"]:
        return None

    start, end = truth["line_range"]
    line = finding.start_line
    if line is not None and (line < start - LINE_SLACK or line > end + LINE_SLACK):
        return None

    structured = finding.issue_type.value == truth["defect_type"]
    if structured:
        in_range = line is not None and start <= line <= end
        return 0.90 + (0.10 if in_range else 0.05)

    if finding.category != truth["category"]:
        return None
    if line is None:
        semantic = _semantic_score(finding, truth)
        return 0.5 + 0.5 * semantic if semantic >= 0.20 else None
    semantic = _semantic_score(finding, truth)
    if semantic < 0.045:
        return None
    line_score = 1.0 if start <= line <= end else 0.5
    return 0.45 + 0.25 * line_score + 0.30 * semantic


# -------------------------------------------------------------------------- unified

TEXT_OVERLAP_THRESHOLD = 0.20


def _evidence_supported(finding: Finding, truth: dict, source: str) -> bool:
    """The cited evidence must literally exist in the changed file."""
    evidence = finding.evidence.strip()
    if len(evidence) < 3:
        return False
    if not source:
        return False
    return evidence in source


def strict_match(finding: Finding, truth: dict, source: str = "") -> str | None:
    """Return the match channel (``structured`` or ``text``) or ``None``.

    Rules, in order:

    1. The finding must be a ``defect``. Advisories never count as detections.
    2. Same ``file_path``.
    3. A concrete ``start_line`` inside the labelled window. A finding with no line
       is not a detection; see ``ALLOW_UNLOCATED`` for the explicit exception.
    4. Verbatim ``evidence`` present in the changed file.
    5. ``issue_type`` equals the labelled ``defect_type`` (``structured``), or, when
       the producer has no classification, the label ``category`` matches and the
       text overlaps the label description (``text``).
    """
    if finding.finding_type is not FindingType.DEFECT:
        return None
    if finding.file_path != truth["file_path"]:
        return None

    start, end = truth["line_range"]
    line = finding.start_line
    if line is None:
        if not truth.get("allow_unlocated"):
            return None
    elif line < start - LINE_SLACK or line > end + LINE_SLACK:
        return None

    if not _evidence_supported(finding, truth, source or truth.get("code", "")):
        return None

    if finding.issue_type.value == truth["defect_type"]:
        return "structured"
    if finding.issue_type.value != "other":
        # A different, specific classification is a wrong diagnosis, not a match.
        return None
    if finding.category != truth["category"]:
        return None
    if _semantic_score(finding, truth) < TEXT_OVERLAP_THRESHOLD:
        return None
    return "text"


def _new_category_stats() -> dict[str, dict[str, int]]:
    return defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "labelled": 0})


def _accumulate_categories(
    per_category: dict[str, dict[str, int]],
    truths: list[dict],
    candidates: list[Finding],
    used_predictions: set[int],
    used_truths: set[int],
    *,
    extra_false_positives: list[Finding] | None = None,
) -> None:
    """Attribute one case's outcome to defect categories.

    Each labelled defect contributes **exactly one** of TP or FN, keyed by its own
    `defect_type`. Each unmatched prediction contributes exactly one FP, keyed by what
    the prediction claimed. This guarantees, per category, `tp + fn == labelled`, which
    an earlier implementation broke by crediting every truth as an FN and then adding a
    TP for each match.
    """
    for truth_index, truth in enumerate(truths):
        bucket = per_category[truth["defect_type"]]
        bucket["labelled"] += 1
        if truth_index in used_truths:
            bucket["tp"] += 1
        else:
            bucket["fn"] += 1
    for prediction_index, finding in enumerate(candidates):
        if prediction_index not in used_predictions:
            per_category[finding.issue_type.value]["fp"] += 1
    for finding in extra_false_positives or []:
        per_category[finding.issue_type.value]["fp"] += 1


def _category_summary(per_category: dict[str, dict[str, int]]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for name, values in sorted(per_category.items()):
        tp, fp, fn = values["tp"], values["fp"], values["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        summary[name] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "labelled": values["labelled"],
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return summary


def _split(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    defects = [item for item in findings if item.finding_type is FindingType.DEFECT]
    advisories = [item for item in findings if item.finding_type is not FindingType.DEFECT]
    return defects, advisories


def _greedy(score_pairs: list[tuple[float, int, int]]) -> tuple[set[int], set[int]]:
    used_predictions: set[int] = set()
    used_truths: set[int] = set()
    for _, prediction_index, truth_index in sorted(score_pairs, reverse=True):
        if prediction_index in used_predictions or truth_index in used_truths:
            continue
        used_predictions.add(prediction_index)
        used_truths.add(truth_index)
    return used_predictions, used_truths


def _legacy_score(cases: list[dict], predicted_by_case: dict[str, list[Finding]], *, include_advisories: bool) -> dict:
    true_positive = false_positive = false_negative = 0
    per_category = _new_category_stats()
    no_defect_findings = 0
    for case in cases:
        predicted = predicted_by_case.get(case["case_id"], [])
        defects, advisories = _split(predicted)
        counted = defects + advisories if include_advisories else defects
        truths = case_defects(case)
        options = []
        for prediction_index, finding in enumerate(counted):
            for truth_index, truth in enumerate(truths):
                score = _legacy_match_score(finding, truth)
                if score is not None:
                    options.append((score, prediction_index, truth_index))
        used_predictions, used_truths = _greedy(options)
        tp = len(used_predictions)
        fp = len(counted) - tp
        fn = len(truths) - len(used_truths)
        true_positive += tp
        false_positive += fp
        false_negative += fn
        _accumulate_categories(
            per_category,
            truths,
            counted,
            used_predictions,
            used_truths,
            extra_false_positives=[] if include_advisories else [],
        )
        if not case_has_defect(case):
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
        "category_recall": {name: values["recall"] for name, values in _category_summary(per_category).items()},
        "category_stats": _category_summary(per_category),
        "false_positives_per_no_defect_pr": no_defect_findings
        / max(1, sum(not case_has_defect(case) for case in cases)),
    }


def _unified_score(
    cases: list[dict],
    predicted_by_case: dict[str, list[Finding]],
    *,
    count_advisories_as_fp: bool,
) -> dict:
    """Score with `strict_match`.

    ``count_advisories_as_fp`` implements the strict reading: only a defect can be
    a true positive, and an advisory is always a false positive, so relabelling
    findings as advice cannot improve either precision or F1.
    """
    true_positive = false_positive = false_negative = 0
    channels = {"structured": 0, "text": 0}
    per_category = _new_category_stats()
    no_defect_findings = 0
    no_defect_cases = max(1, sum(not case_has_defect(case) for case in cases))
    for case in cases:
        predicted = predicted_by_case.get(case["case_id"], [])
        defects, advisories = _split(predicted)
        candidates = defects
        sources = case_sources(case)
        truths = [
            {**truth, "code": sources.get(truth["file_path"], truth.get("code", ""))}
            for truth in case_defects(case)
        ]
        options = []
        channel_by_pair: dict[tuple[int, int], str] = {}
        for prediction_index, finding in enumerate(candidates):
            for truth_index, truth in enumerate(truths):
                channel = strict_match(finding, truth)
                if channel is not None:
                    weight = 1.0 if channel == "structured" else 0.5
                    options.append((weight, prediction_index, truth_index))
                    channel_by_pair[(prediction_index, truth_index)] = channel
        used_predictions, used_truths = _greedy(options)
        for prediction_index in used_predictions:
            for truth_index in used_truths:
                channel = channel_by_pair.get((prediction_index, truth_index))
                if channel:
                    channels[channel] += 1
                    break

        tp = len(used_predictions)
        fp = len(candidates) - tp + (len(advisories) if count_advisories_as_fp else 0)
        fn = len(truths) - len(used_truths)
        true_positive += tp
        false_positive += fp
        false_negative += fn
        _accumulate_categories(
            per_category,
            truths,
            candidates,
            used_predictions,
            used_truths,
            extra_false_positives=list(advisories) if count_advisories_as_fp else [],
        )
        if not case_has_defect(case):
            no_defect_findings += len(candidates) + (len(advisories) if count_advisories_as_fp else 0)

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
        "category_recall": {name: values["recall"] for name, values in _category_summary(per_category).items()},
        "category_stats": _category_summary(per_category),
        "false_positives_per_no_defect_pr": no_defect_findings / no_defect_cases,
        "match_channels": dict(channels),
    }


def evaluate_cases(
    cases: list[dict],
    predicted_by_case: dict[str, list[Finding]],
    *,
    matcher: str = "unified",
) -> dict:
    """Score a run.

    ``matcher="legacy"`` reproduces the published v1 numbers.
    ``matcher="unified"`` is the default and the only valid choice for comparisons.

    Returned keys:

    * ``defect_precision`` / ``defect_recall`` / ``f1_score`` — unified scorer,
      advisories excluded from the denominator, so they describe confirmed defects.
    * ``strict_precision`` / ``strict_recall`` / ``strict_f1`` — unified scorer with
      advisories counted as false positives.
    * ``legacy_*`` — the frozen scorer, for history only.
    """
    chosen = _legacy_score if matcher == "legacy" else _unified_score
    defect = chosen(cases, predicted_by_case, include_advisories=False) if matcher == "legacy" else chosen(
        cases, predicted_by_case, count_advisories_as_fp=False
    )
    strict = chosen(cases, predicted_by_case, include_advisories=True) if matcher == "legacy" else chosen(
        cases, predicted_by_case, count_advisories_as_fp=True
    )
    legacy = _legacy_score(cases, predicted_by_case, include_advisories=False)

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
            "is_real_defect": case_has_defect(case),
            "labelled_defects": len(case_defects(case)),
            "defect_count": len(defects),
            "advisory_count": len(advisories),
            "findings": [item.model_dump(mode="json") for item in predicted],
        })
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "matcher": matcher,
        "tp": defect["tp"],
        "fp": defect["fp"],
        "fn": defect["fn"],
        "defect_precision": defect["precision"],
        "defect_recall": defect["recall"],
        "f1_score": defect["f1"],
        "category_recall": defect["category_recall"],
        "category_stats": defect["category_stats"],
        "strict_category_stats": strict["category_stats"],
        "false_positives_per_no_defect_pr": defect["false_positives_per_no_defect_pr"],
        "match_channels": defect.get("match_channels", {}),
        "strict_tp": strict["tp"],
        "strict_fp": strict["fp"],
        "strict_fn": strict["fn"],
        "strict_precision": strict["precision"],
        "strict_recall": strict["recall"],
        "strict_f1": strict["f1"],
        "legacy_tp": legacy["tp"],
        "legacy_fp": legacy["fp"],
        "legacy_fn": legacy["fn"],
        "legacy_precision": legacy["precision"],
        "legacy_recall": legacy["recall"],
        "legacy_f1": legacy["f1"],
        "advisory_count": advisory_total,
        "advisory_rate": advisory_total / finding_total if finding_total else 0.0,
        "needs_human_review_count": review_total,
        "mean_findings_per_pr": finding_total / max(1, len(cases)),
        "details": details,
    }


def _agent_stats(results: list[dict], cases: list[dict]) -> dict[str, dict[str, float]]:
    """Per-agent accepted ratio and rejection rate, using the unified matcher."""
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
                if any(strict_match(finding, truth) is not None for truth in case_defects(case)):
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


def _pre_verifier_scores(results: list[dict], cases: list[dict], matcher: str) -> dict:
    """Score the agent output before verification, for a before/after comparison."""
    raw_by_case: dict[str, list[Finding]] = {}
    for item in results:
        payload = item.get("raw_findings")
        if payload is None:
            return {}
        raw_by_case[item["case_id"]] = [Finding(**entry) for entry in payload]
    if not raw_by_case:
        return {}
    scores = evaluate_cases(cases, raw_by_case, matcher=matcher)
    return {
        "pre_verifier_precision": scores["defect_precision"],
        "pre_verifier_recall": scores["defect_recall"],
        "pre_verifier_f1": scores["f1_score"],
        "pre_verifier_fp": scores["fp"],
        "pre_verifier_finding_count": sum(len(items) for items in raw_by_case.values()),
    }


def aggregate_performance(results: list[dict], cases: list[dict], *, matcher: str = "unified") -> dict:
    metrics = [item["run_metrics"] for item in results]
    latencies = sorted(float(item.get("execution_seconds", 0)) for item in metrics)
    p95_index = max(0, math.ceil(0.95 * len(latencies)) - 1) if latencies else 0
    count = max(1, len(metrics))
    all_predictions = [finding for item in results for finding in item["findings"]]
    merged = evaluate_cases(
        cases, {item["case_id"]: item["findings"] for item in results}, matcher=matcher
    )
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
    defect_cases = sum(1 for case in cases if case_has_defect(case))
    labelled_defects = sum(len(case_defects(case)) for case in cases)
    pre = _pre_verifier_scores(results, cases, matcher)
    return {
        **pre,
        "post_verifier_precision": merged["defect_precision"],
        "post_verifier_recall": merged["defect_recall"],
        **{key: value for key, value in merged.items() if key != "details"},
        "mean_input_tokens": input_tokens / count,
        "mean_output_tokens": output_tokens / count,
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
        "defect_cases": defect_cases,
        "labelled_defects": labelled_defects,
        "runs": len(results),
        "finding_count": len(all_predictions),
    }


def _price(name: str) -> float | None:
    try:
        return max(0.0, float(os.environ[name]))
    except (KeyError, ValueError):
        return None
