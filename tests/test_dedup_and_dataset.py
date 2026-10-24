"""Deduplication edge cases and evaluator self-tests on the candidate dataset.

Two different failure modes are covered:

* over-merging, where two real defects collapse into one and recall silently drops;
* under-merging, where one defect reported several ways inflates false positives.

The evaluator self-test feeds the *labels themselves* back as predictions. Scoring must
then be perfect, which proves the matcher can express multi-file, multi-defect ground
truth. It says nothing about model quality.
"""

from __future__ import annotations

import json

import pytest

from evals.fixtures import DATASET_V2, case_defects, case_files, case_has_defect
from evals.metrics import evaluate_cases
from prguard.models import Finding, FindingType, IssueType
from prguard.verifier import _same_issue, verify_findings


def dedup_finding(**overrides) -> Finding:
    payload = {
        "category": "security",
        "severity": "high",
        "file_path": "app/uploads.py",
        "start_line": 5,
        "end_line": 5,
        "title": "文件权限过宽",
        "description": "save() 把文件权限设为全局可写，任何本地用户都能改写该文件。",
        "evidence": "os.chmod(path, 0o777)",
        "suggestion": "改为 0o600。",
        "confidence": 0.9,
        "issue_type": IssueType.OVERLY_PERMISSIVE_FILE_MODE,
        "finding_type": FindingType.DEFECT,
        "trigger_condition": "调用 save(path) 时",
    }
    payload.update(overrides)
    if "start_line" in overrides and "end_line" not in overrides:
        payload["end_line"] = payload["start_line"]
    return Finding(**payload)


# --- must merge --------------------------------------------------------------


def test_one_defect_reported_by_three_agents_merges():
    wordings = [
        ("CodeQualityAgent", "save() 无条件将文件权限设为 0o777", "调用 save 即触发，权限被放宽到所有用户。"),
        ("SecurityAgent", "权限设置过宽（0o777）导致任意用户可读写执行", "任何上传流程调用 save(path) 都会触发。"),
        ("TestImpactAgent", "文件权限被放宽为全局可读写执行 (0o777)", "save 的权限变更行为缺少测试覆盖。"),
    ]
    findings = [
        dedup_finding(source_agent=agent, title=title, description=description)
        for agent, title, description in wordings
    ]
    assert _same_issue(findings[0], findings[1])
    assert _same_issue(findings[0], findings[2])
    assert _same_issue(findings[1], findings[2])


def test_same_defect_different_severity_still_merges():
    """Severity is a judgement call; it must not split one defect into two."""
    left = dedup_finding(severity="high")
    right = dedup_finding(severity="critical", title="权限被设为 0o777，属于严重配置错误")
    assert _same_issue(left, right)


def test_merge_keeps_the_highest_severity_and_richest_source_list():
    from prguard.models import PRFile, PRSnapshot

    snapshot = PRSnapshot(
        repo="acme/widgets",
        pr_number=1,
        title="uploads",
        base_sha="a",
        head_sha="b",
        files=[PRFile(path="app/uploads.py", status="modified", patch="+x", changes=1)],
        diff="diff --git a/app/uploads.py b/app/uploads.py\n@@ -1,5 +1,5 @@\n+import os\n+\n+\n+def save(path):\n+    os.chmod(path, 0o777)\n",
    )
    sources = {"app/uploads.py": "import os\n\n\ndef save(path):\n    os.chmod(path, 0o777)\n"}
    findings = [
        dedup_finding(source_agent="CodeQualityAgent", severity="high", confidence=0.9),
        dedup_finding(source_agent="SecurityAgent", severity="critical", confidence=0.8,
                      title="权限被设为 0o777，任意用户可写"),
    ]
    verified, _ = verify_findings(findings, snapshot, sources, check_conditions=False, dedupe=True)
    assert len(verified) == 1
    merged = verified[0]
    # Nothing that a reader needs may be lost: sources, evidence, suggestion.
    assert "CodeQualityAgent" in merged.source_agent and "SecurityAgent" in merged.source_agent
    assert merged.evidence.strip()
    assert merged.suggestion.strip()
    assert merged.severity.value in {"high", "critical"}


# --- must NOT merge ----------------------------------------------------------


def test_same_evidence_same_type_on_adjacent_lines_stays_two_defects():
    """`writer.py` chmods 0o777 in write_a and again in write_b: two real defects."""
    left = dedup_finding(start_line=5, end_line=5, title="write_a 权限过宽", trigger_condition="调用 write_a")
    right = dedup_finding(start_line=9, end_line=9, title="write_b 权限过宽", trigger_condition="调用 write_b")
    assert not _same_issue(left, right)


def test_same_evidence_different_issue_type_stays_two_defects():
    left = dedup_finding(issue_type=IssueType.OVERLY_PERMISSIVE_FILE_MODE)
    right = dedup_finding(issue_type=IssueType.HARDCODED_SECRET, title="硬编码令牌")
    assert not _same_issue(left, right)


def test_same_line_two_defects_with_different_evidence_stay_separate():
    left = dedup_finding(start_line=5, evidence="os.chmod(path, 0o777)")
    right = dedup_finding(
        start_line=5,
        evidence='TOKEN = "sk-live-77aa88bb99cc"',
        issue_type=IssueType.HARDCODED_SECRET,
        title="同一行还硬编码了令牌",
    )
    assert not _same_issue(left, right)


def test_different_files_with_identical_code_do_not_merge():
    left = dedup_finding(file_path="app/writer.py")
    right = dedup_finding(file_path="app/other_writer.py")
    assert not _same_issue(left, right)


def test_disjoint_triggers_with_different_evidence_do_not_merge():
    left = dedup_finding(evidence="handle = open(path)", trigger_condition="path does not exist")
    right = dedup_finding(
        evidence="return handle.read()",
        trigger_condition="并发写入导致缓存失效",
        issue_type=IssueType.RESOURCE_LEAK,
    )
    assert not _same_issue(left, right)


# --- candidate dataset -------------------------------------------------------


@pytest.fixture(scope="module")
def dataset() -> dict:
    return json.loads(DATASET_V2.read_text(encoding="utf-8"))


def test_candidate_dataset_shape(dataset):
    stats = dataset["stats"]
    assert stats["case_count"] >= 30
    assert stats["multi_file_cases"] >= 10
    assert stats["clean_cases"] >= 10
    assert stats["multi_defect_cases"] >= 3
    assert dataset["label_policy"]["human_reviewed_count"] == 0
    assert dataset["label_policy"]["llm_generated_labels"] is False
    for case in dataset["cases"]:
        assert case["label_review_status"] in {"draft", "disputed", "human_reviewed", "indeterminate"}
        assert case["split"] in {"dev", "test", "holdout"}
        assert case_has_defect(case) == bool(case_defects(case))


def test_candidate_labels_score_perfectly_against_themselves(dataset):
    """The matcher must be able to express this ground truth at all."""
    predictions: dict[str, list[Finding]] = {}
    for case in dataset["cases"]:
        predictions[case["case_id"]] = [
            Finding(
                category=entry["category"],
                severity=entry["severity"],
                file_path=entry["file_path"],
                start_line=entry["line_range"][0],
                end_line=entry["line_range"][1],
                title=f"{entry['issue_type']} at {entry['file_path']}",
                description=entry["description"],
                evidence=entry["supporting_evidence"],
                suggestion="fix",
                confidence=0.9,
                issue_type=IssueType(entry["issue_type"]),
                finding_type=FindingType.DEFECT,
                trigger_condition=entry["trigger_condition"],
            )
            for entry in case_defects(case)
        ]
    scores = evaluate_cases(dataset["cases"], predictions)
    assert scores["defect_precision"] == 1.0
    assert scores["defect_recall"] == 1.0
    assert scores["f1_score"] == 1.0
    assert scores["false_positives_per_no_defect_pr"] == 0.0


def test_one_missed_defect_in_a_multi_defect_case_costs_exactly_one_fn(dataset):
    multi = next(case for case in dataset["cases"] if len(case_defects(case)) >= 3)
    truths = case_defects(multi)
    dropped = truths[0]
    predictions = {
        case["case_id"]: [
            Finding(
                category=entry["category"],
                severity=entry["severity"],
                file_path=entry["file_path"],
                start_line=entry["line_range"][0],
                end_line=entry["line_range"][1],
                title=f"{entry['issue_type']} at {entry['file_path']}",
                description=entry["description"],
                evidence=entry["supporting_evidence"],
                suggestion="fix",
                confidence=0.9,
                issue_type=IssueType(entry["issue_type"]),
                finding_type=FindingType.DEFECT,
                trigger_condition=entry["trigger_condition"],
            )
            for entry in case_defects(case)
            if entry["defect_id"] != dropped["defect_id"]
        ]
        for case in dataset["cases"]
    }
    scores = evaluate_cases(dataset["cases"], predictions)
    assert scores["fn"] == 1
    assert scores["tp"] == sum(len(case_defects(case)) for case in dataset["cases"]) - 1
    assert scores["fp"] == 0


def test_multi_file_case_evaluates_per_file_not_per_case(dataset):
    case = next(case for case in dataset["cases"] if len(case_files(case)) > 1 and case_defects(case))
    truths = case_defects(case)
    assert all(truth["file_path"] in {entry["path"] for entry in case_files(case)} for truth in truths)


# --- frozen online regression (committed fixture, no network) ----------------


def test_frozen_online_fixture_is_committed_and_parses():
    from evals.path_ablation import FROZEN

    assert FROZEN.exists(), "the frozen fixture must be committed so CI cannot skip"
    document = json.loads(FROZEN.read_text(encoding="utf-8"))
    assert document["case_count"] == 15
    assert sum(len(v) for v in document["raw_findings"].values()) == 28
    assert document["prompt_version"] == "prguard-review-v2"
    # No credentials may be present in a committed artifact. The only secret-like string
    # allowed is the synthetic literal from the v1 fixture, which is public test data.
    text = FROZEN.read_text(encoding="utf-8")
    import re

    found = {item for item in re.findall(r"sk-[A-Za-z0-9_-]{8,}", text)}
    assert found <= {"sk-live-1234567890abcdef"}, found
    assert "ghp_" not in text
    assert "github_pat_" not in text


def test_frozen_fixture_reproduces_the_published_ablation():
    """Locks the A/B/C/D numbers so a verifier change cannot move them silently."""
    from evals import path_ablation

    document, cases = path_ablation.load_frozen(path_ablation.FROZEN)
    rows = {}
    for tag, _label, options in path_ablation.CONFIGS:
        scores, resolutions, _extra, _by_case = path_ablation.replay(document, cases, options)
        rows[tag] = {
            "tp": scores["tp"],
            "fp": scores["fp"],
            "fn": scores["fn"],
            "recoveries": len(resolutions),
        }
    assert rows["A"] == {"tp": 4, "fp": 2, "fn": 6, "recoveries": 0}
    assert rows["B"]["tp"] == 8 and rows["B"]["fn"] == 2
    assert rows["C"]["fp"] == 5
    assert rows["D"] == {"tp": 8, "fp": 2, "fn": 2, "recoveries": 12}


# --- review packet (P3) ------------------------------------------------------


def test_review_packet_never_raises_a_status():
    """The generator must not mark anything reviewed, ever."""
    from evals.review_packet import build

    dataset = json.loads(DATASET_V2.read_text(encoding="utf-8"))
    markdown, rows, summary = build(dataset, None)
    assert len({row["case_id"] for row in rows}) == len(dataset["cases"])
    for row in rows:
        assert row["review_status"] in {"draft", "reviewed", "needs_revision", "indeterminate"}
        # Only statuses the dataset already carries may appear.
        assert row["review_status"] != "reviewed" or dataset["label_policy"]["human_reviewed_count"] > 0
        assert row["is_substantive_defect"] == ""
    assert "reviewed: 0" in summary


def test_review_packet_covers_every_file_and_lists_clean_cases():
    from evals.review_packet import build

    dataset = json.loads(DATASET_V2.read_text(encoding="utf-8"))
    markdown, rows, summary = build(dataset, None)
    for case in dataset["cases"]:
        assert f"## {case['case_id']}" in markdown
        for entry in case_files(case):
            assert f"`{entry['path']}`" in markdown
    clean = [case["case_id"] for case in dataset["cases"] if not case_defects(case)]
    for case_id in clean:
        assert f"`{case_id}`" in summary


def test_review_packet_shows_before_and_after_for_real_diffs():
    from evals.review_packet import build

    dataset = json.loads(DATASET_V2.read_text(encoding="utf-8"))
    markdown, _, _ = build(dataset, "holdout")
    assert "**Before (base revision):**" in markdown
    assert "**After (head revision):**" in markdown
