"""Hand-computed TP/FP/FN assertions for the scorer.

Every test states the expected counts numerically before asserting them, so a change in
the metric cannot pass by accident. `f1 == 1.0` is never used as the only assertion.

Invariants checked throughout:

* TP is a labelled defect that was matched, exactly once.
* FN is a labelled defect that was not matched.
* FP is a prediction that matched nothing.
* per category: tp + fn == labelled defects of that category.
* a prediction matches at most one truth, and a truth is matched by at most one prediction.
"""

from __future__ import annotations

import pytest

from evals.metrics import evaluate_cases
from prguard.models import Finding, FindingType, IssueType

CODE = "line1 = 1\nline2 = 2\nline3 = 3\nline4 = 4\n"


def truth(defect_type: str, line: int, *, category: str = "security", path: str = "a.py") -> dict:
    return {
        "defect_id": f"d-{defect_type}-{line}",
        "defect_type": defect_type,
        "issue_type": defect_type,
        "category": category,
        "file_path": path,
        "line_range": [line, line],
        "description": f"labelled {defect_type} defect",
        "trigger_condition": "stated",
        "supporting_evidence": f"line{line}",
    }


def case(case_id: str, defects: list[dict], *, files: list[dict] | None = None) -> dict:
    return {
        "case_id": case_id,
        "has_defect": bool(defects),
        "files": files or [{"path": "a.py", "code": CODE}],
        "defects": defects,
    }


def pred(
    issue_type: IssueType,
    line: int,
    *,
    finding_type: FindingType = FindingType.DEFECT,
    category: str = "security",
    path: str = "a.py",
) -> Finding:
    return Finding(
        category=category,
        severity="high",
        file_path=path,
        start_line=line,
        end_line=line,
        title=f"{issue_type.value} reported here",
        description=f"description of {issue_type.value}",
        evidence=f"line{line}",
        suggestion="fix it now",
        confidence=0.9,
        issue_type=issue_type,
        finding_type=finding_type,
        trigger_condition="stated",
    )


def score(cases: list[dict], predictions: dict[str, list[Finding]]) -> dict:
    return evaluate_cases(cases, predictions)


# --- 1. two defects of different categories, both found ----------------------


def test_two_categories_both_hit():
    c = case("c1", [truth("sql_injection", 1), truth("division_by_zero", 3, category="bug")])
    result = score([c], {"c1": [pred(IssueType.SQL_INJECTION, 1), pred(IssueType.DIVISION_BY_ZERO, 3, category="bug")]})
    # Expected: 2 truths, both matched, no stray predictions.
    assert (result["tp"], result["fp"], result["fn"]) == (2, 0, 0)
    stats = result["category_stats"]
    assert stats["sql_injection"]["tp"] == 1 and stats["sql_injection"]["fn"] == 0
    assert stats["division_by_zero"]["tp"] == 1 and stats["division_by_zero"]["fn"] == 0
    for name, values in stats.items():
        assert values["tp"] + values["fn"] == values["labelled"]


# --- 2. two defects of the same category, one found -------------------------


def test_two_same_category_defects_only_one_hit():
    c = case("c2", [truth("overly_permissive_file_mode", 1), truth("overly_permissive_file_mode", 3)])
    result = score([c], {"c2": [pred(IssueType.OVERLY_PERMISSIVE_FILE_MODE, 1)]})
    # Expected: 2 truths, 1 matched -> tp 1, fn 1, fp 0.
    assert (result["tp"], result["fp"], result["fn"]) == (1, 0, 1)
    stats = result["category_stats"]["overly_permissive_file_mode"]
    assert (stats["tp"], stats["fn"], stats["labelled"]) == (1, 1, 2)
    assert stats["recall"] == pytest.approx(0.5)
    assert stats["precision"] == pytest.approx(1.0)


# --- 3. the same defect predicted twice -------------------------------------


def test_duplicate_prediction_scores_one_tp_and_one_fp():
    c = case("c3", [truth("sql_injection", 2)])
    result = score([c], {"c3": [pred(IssueType.SQL_INJECTION, 2), pred(IssueType.SQL_INJECTION, 2)]})
    # Expected: 1 truth (matched once), 1 surplus prediction.
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 0)
    stats = result["category_stats"]["sql_injection"]
    assert (stats["tp"], stats["fp"], stats["fn"]) == (1, 1, 0)
    assert stats["precision"] == pytest.approx(0.5)


# --- 4. a defect reported under the wrong issue type ------------------------


def test_misclassified_prediction_is_a_false_positive():
    c = case("c4", [truth("sql_injection", 2)])
    result = score([c], {"c4": [pred(IssueType.DIVISION_BY_ZERO, 2, category="bug")]})
    # The prediction is on the right line with the right evidence, but names the wrong
    # kind of problem, so strict_match rejects it: 0 tp, 1 fp, 1 fn.
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 1)
    stats = result["category_stats"]
    assert stats["sql_injection"]["fn"] == 1 and stats["sql_injection"]["tp"] == 0
    assert stats["division_by_zero"]["fp"] == 1


# --- 5. a PR with no defect at all ------------------------------------------


def test_clean_pr_charges_only_false_positives():
    c = case("c5", [])
    result = score([c], {"c5": [pred(IssueType.SQL_INJECTION, 1)]})
    assert (result["tp"], result["fp"], result["fn"]) == (0, 1, 0)
    # No labelled defect, so no category should claim a truth.
    assert result["category_stats"]["sql_injection"]["labelled"] == 0
    assert result["category_stats"]["sql_injection"]["fn"] == 0
    assert result["false_positives_per_no_defect_pr"] == pytest.approx(1.0)


# --- 6. multi-defect PR mixing an advisory ----------------------------------


def test_multi_defect_pr_with_advisory_prediction():
    c = case("c6", [truth("sql_injection", 1), truth("resource_leak", 3, category="bug")])
    predictions = [
        pred(IssueType.SQL_INJECTION, 1),
        pred(IssueType.RESOURCE_LEAK, 3, category="bug"),
        pred(IssueType.OTHER, 2, finding_type=FindingType.ADVISORY, category="quality"),
    ]
    result = score([c], {"c6": predictions})
    # Defect view: 2 tp, 0 fp, 0 fn. Advisory is excluded from the denominator.
    assert (result["tp"], result["fp"], result["fn"]) == (2, 0, 0)
    assert result["advisory_count"] == 1
    # Strict view: the advisory becomes a false positive.
    assert (result["strict_tp"], result["strict_fp"], result["strict_fn"]) == (2, 1, 0)
    assert result["strict_f1"] < result["f1_score"]
    assert result["strict_category_stats"]["other"]["fp"] == 1


# --- invariant sweep over a mixed dataset -----------------------------------


def test_every_category_satisfies_tp_plus_fn_equals_labelled():
    cases = [
        case("m1", [truth("sql_injection", 1), truth("sql_injection", 3)]),
        case("m2", [truth("resource_leak", 1, category="bug")]),
        case("m3", [truth("hardcoded_secret", 2), truth("unsafe_command", 4), truth("boundary_condition", 3, category="bug")]),
        case("m4", []),
    ]
    predictions = {
        "m1": [pred(IssueType.SQL_INJECTION, 1)],
        "m2": [],
        "m3": [pred(IssueType.HARDCODED_SECRET, 2), pred(IssueType.BOUNDARY_CONDITION, 3, category="bug")],
        "m4": [pred(IssueType.OTHER, 1)],
    }
    result = score(cases, predictions)
    # Hand count: m1 1 tp 1 fn, m2 0 tp 1 fn, m3 2 tp 1 fn, m4 1 fp.
    assert (result["tp"], result["fp"], result["fn"]) == (3, 1, 3)
    labelled = sum(values["labelled"] for values in result["category_stats"].values())
    assert labelled == 6
    for name, values in result["category_stats"].items():
        assert values["tp"] + values["fn"] == values["labelled"], name
    # Category totals must add up to the global counts, with no double counting.
    assert sum(values["tp"] for values in result["category_stats"].values()) == result["tp"]
    assert sum(values["fn"] for values in result["category_stats"].values()) == result["fn"]
    assert sum(values["fp"] for values in result["category_stats"].values()) == result["fp"]


def test_one_prediction_cannot_satisfy_two_truths():
    """Two labelled defects on nearby lines, one prediction: exactly one TP."""
    c = case("d1", [truth("sql_injection", 1), truth("sql_injection", 3)])
    result = score([c], {"d1": [pred(IssueType.SQL_INJECTION, 1)]})
    assert result["tp"] == 1
    assert result["fn"] == 1
    assert result["category_stats"]["sql_injection"]["tp"] == 1


def test_advisory_never_becomes_a_true_positive():
    c = case("a1", [truth("sql_injection", 1)])
    result = score([c], {"a1": [pred(IssueType.SQL_INJECTION, 1, finding_type=FindingType.ADVISORY)]})
    assert result["tp"] == 0
    assert result["fp"] == 0  # excluded from the defect denominator
    assert result["strict_fp"] == 1
    assert result["strict_tp"] == 0


def test_category_precision_matches_global_precision_when_one_category():
    c = case("p1", [truth("sql_injection", 1)])
    predictions = [pred(IssueType.SQL_INJECTION, 1), pred(IssueType.OTHER, 3)]
    result = score([c], {"p1": predictions})
    # Hand count: tp 1, fp 1 (the `other` prediction matches nothing).
    assert (result["tp"], result["fp"]) == (1, 1)
    assert result["defect_precision"] == pytest.approx(0.5)
    stats = result["category_stats"]
    total_tp = sum(values["tp"] for values in stats.values())
    total_fp = sum(values["fp"] for values in stats.values())
    assert total_tp / (total_tp + total_fp) == pytest.approx(result["defect_precision"])
