"""Guards the published prguard-review-v1 numbers against evaluator drift.

Legacy metrics exist only so historical records stay reproducible. If this test
fails, the frozen matcher was edited, and every previously published number has to
be re-measured and re-published rather than quietly replaced.

The recorded run lives in `artifacts/`, which is git-ignored, so the test skips
when it is absent. Reproduce it with the online command in docs/benchmark.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.metrics import _legacy_score, _unified_score
from evals.replay_v1 import to_finding
from prguard.models import Finding

RECORDED = Path("artifacts/evals/online/20261024T081509Z/results.json")

pytestmark = pytest.mark.skipif(not RECORDED.exists(), reason="recorded v1 run not present")


def _recorded():
    document = json.loads(RECORDED.read_text(encoding="utf-8"))
    dataset = json.loads(Path("evals/datasets/prguard_v1.json").read_text(encoding="utf-8"))
    by_case: dict[str, list[Finding]] = {}
    for record in document["cases"]["dynamic"]:
        by_case[record["case_id"]] = [to_finding(raw) for raw in record["findings"]]
    return dataset["cases"], by_case


def test_legacy_matcher_reproduces_the_published_v1_scores():
    cases, by_case = _recorded()
    legacy = _legacy_score(cases, by_case, include_advisories=False)
    assert legacy["tp"] == 7
    assert legacy["fp"] == 68
    assert legacy["fn"] == 3
    assert round(legacy["precision"], 3) == 0.093
    assert round(legacy["recall"], 3) == 0.700
    assert round(legacy["f1"], 3) == 0.165


def test_legacy_numbers_do_not_depend_on_the_new_schema_fields():
    """The replay must not infer issue_type or a line number from the labels."""
    cases, _ = _recorded()
    document = json.loads(RECORDED.read_text(encoding="utf-8"))
    for record in document["cases"]["dynamic"]:
        case = next(item for item in cases if item["case_id"] == record["case_id"])
        for raw in record["findings"]:
            finding = to_finding(raw)
            assert finding.issue_type.value == "other"
            assert finding.finding_type.value == "defect"
            if raw.get("start_line") is None:
                assert finding.start_line is None
                assert case["line_range"][0] != finding.start_line


def test_unified_matcher_is_stricter_than_legacy_on_the_same_output():
    cases, by_case = _recorded()
    legacy = _legacy_score(cases, by_case, include_advisories=False)
    unified = _unified_score(cases, by_case, count_advisories_as_fp=False)
    assert unified["tp"] < legacy["tp"]
    assert unified["fp"] >= legacy["fp"]
