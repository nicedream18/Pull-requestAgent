from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from evals.import_reviews import (
    ReviewImportError,
    dataset_review_gate,
    inspect_reviews,
    render_progress,
    write_audit_snapshot,
    write_reviewed_dataset,
)
from evals.fixtures import DATASET_V2

FIELDS = [
    "case_id",
    "split",
    "defect_id",
    "file_path",
    "line_range",
    "issue_type",
    "severity",
    "evidence",
    "trigger_condition",
    "is_substantive_defect",
    "other_unlabelled_defects",
    "reviewer_notes",
    "review_status",
    "reviewer_id",
    "reviewed_at",
    "review_version",
]


def fixture_data(tmp_path: Path, rows: list[dict]) -> tuple[Path, Path]:
    dataset = {
        "dataset_version": "test-candidate",
        "cases": [
            {
                "case_id": "bug-case",
                "split": "dev",
                "files": [{"path": "app.py", "code": "unsafe()\n"}],
                "defects": [
                    {
                        "defect_id": "bug-case-d1",
                        "file_path": "app.py",
                        "line_range": [1, 1],
                        "issue_type": "unsafe_command",
                        "severity": "high",
                        "supporting_evidence": "unsafe()",
                    }
                ],
            },
            {
                "case_id": "clean-case",
                "split": "test",
                "files": [{"path": "readme.md", "code": "Safe.\n"}],
                "defects": [],
            },
        ],
    }
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
    csv_path = tmp_path / "candidates.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path, dataset_path


def decision(case_id: str, defect_id: str, status: str, opinion: str, **updates) -> dict:
    row = dict.fromkeys(FIELDS, "")
    row.update(
        {
            "case_id": case_id,
            "split": "dev" if case_id == "bug-case" else "test",
            "defect_id": defect_id,
            "file_path": "app.py" if defect_id else "readme.md",
            "line_range": "1-1" if defect_id else "",
            "issue_type": "unsafe_command" if defect_id else "",
            "severity": "high" if defect_id else "",
            "evidence": "unsafe()" if defect_id else "",
            "trigger_condition": "calling unsafe()" if defect_id else "",
            "is_substantive_defect": opinion,
            "review_status": status,
        }
    )
    row.update(updates)
    return row


def reviewed_metadata(**updates) -> dict:
    return {
        "reviewer_id": "human-reviewer-1",
        "reviewed_at": "2026-10-24T11:30:00+08:00",
        "review_version": "review-protocol-v1",
        **updates,
    }


def test_current_candidate_sheet_has_consistent_case_and_defect_ids():
    result = inspect_reviews()
    assert result["summary"]["case_count"] == 36
    assert result["issues"] == []
    assert result["summary"]["ready_for_freeze"] is False


def test_progress_report_uses_repo_relative_paths_when_available(tmp_path, monkeypatch):
    rows = [decision("bug-case", "bug-case-d1", "draft", ""), decision("clean-case", "", "draft", "")]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    csv_path, dataset_path = fixture_data(data_dir, rows)
    monkeypatch.chdir(tmp_path)

    report = render_progress(inspect_reviews(csv_path, dataset_path), csv_path, dataset_path)

    assert "`data/dataset.json`" in report
    assert "`data/candidates.csv`" in report
    assert str(tmp_path) not in report


def test_candidate_dataset_is_blocked_until_every_case_is_human_reviewed():
    dataset = json.loads(DATASET_V2.read_text(encoding="utf-8"))
    gate = dataset_review_gate(dataset)
    assert gate["required"] is True
    assert gate["eligible"] is False
    assert len(gate["pending_cases"]) == 36


def test_development_fixture_dataset_remains_a_pipeline_regression_dataset():
    assert dataset_review_gate({"cases": [{"case_id": "fixture"}]})["eligible"] is True


@pytest.mark.parametrize("module_name", ["evals.run", "evals.ablation"])
def test_candidate_dataset_cannot_enter_scored_pipeline_while_unreviewed(module_name, tmp_path):
    import importlib

    module = importlib.import_module(module_name)
    arguments = ["--mode", "offline", "--dataset", str(DATASET_V2), "--output", str(tmp_path / "results")]
    with pytest.raises(SystemExit) as exc:
        module.main(arguments)
    assert exc.value.code == 2
    assert not (tmp_path / "results").exists()


def test_all_cases_including_clean_cases_are_required_for_freeze(tmp_path):
    rows = [
        decision("bug-case", "bug-case-d1", "reviewed", "yes", **reviewed_metadata()),
        decision("clean-case", "", "reviewed", "no", **reviewed_metadata()),
    ]
    csv_path, dataset_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, dataset_path)
    assert result["issues"] == []
    assert result["summary"]["reviewed_cases"] == 2
    assert result["summary"]["reviewed_clean_cases"] == 1
    assert result["summary"]["ready_for_freeze"] is True


def test_conflicting_duplicate_decisions_for_same_defect_are_rejected(tmp_path):
    common = reviewed_metadata()
    rows = [
        decision("bug-case", "bug-case-d1", "reviewed", "yes", **common),
        decision("bug-case", "bug-case-d1", "needs_revision", "yes", reviewer_notes="line needs correction", **common),
        decision("clean-case", "", "reviewed", "no", **reviewed_metadata()),
    ]
    csv_path, dataset_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, dataset_path)
    assert any("conflicting decisions" in issue for issue in result["issues"])


def test_unknown_case_and_defect_ids_are_rejected(tmp_path):
    rows = [
        decision("unknown-case", "unknown-d1", "draft", ""),
        decision("clean-case", "", "draft", ""),
    ]
    csv_path, dataset_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, dataset_path)
    assert any("unknown case_id" in issue for issue in result["issues"])
    assert any("missing candidate rows" in issue and "bug-case" in issue for issue in result["issues"])


def test_clean_case_cannot_be_marked_reviewed_as_defective_without_label_revision(tmp_path):
    rows = [
        decision("bug-case", "bug-case-d1", "reviewed", "yes", **reviewed_metadata()),
        decision("clean-case", "", "reviewed", "yes", **reviewed_metadata()),
    ]
    csv_path, dataset_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, dataset_path)
    assert any("conflicts with the candidate label" in issue for issue in result["issues"])


def test_unresolved_rows_do_not_become_reviewed_or_freeze_eligible(tmp_path):
    rows = [
        decision("bug-case", "bug-case-d1", "indeterminate", "indeterminate", **reviewed_metadata(reviewer_notes="Need repository contract.")),
        decision("clean-case", "", "draft", ""),
    ]
    csv_path, dataset_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, dataset_path)
    assert result["summary"]["reviewed_cases"] == 0
    assert result["summary"]["cases_needing_human_work"] == 2
    assert result["summary"]["ready_for_freeze"] is False


def test_audit_snapshot_is_append_only(tmp_path):
    rows = [
        decision("bug-case", "bug-case-d1", "reviewed", "yes", **reviewed_metadata()),
        decision("clean-case", "", "reviewed", "no", **reviewed_metadata()),
    ]
    csv_path, dataset_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, dataset_path)
    audit_path = tmp_path / "audit.json"
    write_audit_snapshot(result, audit_path)
    archived = json.loads(audit_path.read_text(encoding="utf-8"))
    assert archived["decisions"][0]["reviewer_id"] == "human-reviewer-1"
    assert archived["decisions"][0]["reviewed_at"] == "2026-10-24T11:30:00+08:00"
    with pytest.raises(FileExistsError):
        write_audit_snapshot(result, audit_path)


def test_build_reviewed_dataset_writes_a_new_audited_version(tmp_path):
    rows = [
        decision("bug-case", "bug-case-d1", "reviewed", "yes", **reviewed_metadata()),
        decision("clean-case", "", "reviewed", "no", **reviewed_metadata()),
    ]
    csv_path, source_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, source_path)
    output_path = tmp_path / "reviewed" / "dataset.json"

    write_reviewed_dataset(result, source_path, output_path)

    reviewed = json.loads(output_path.read_text(encoding="utf-8"))
    assert dataset_review_gate(reviewed)["eligible"] is True
    assert reviewed["dataset_version"].startswith("test-candidate-reviewed-")
    assert reviewed["review_audit"]["decisions"][0]["reviewer_id"] == "human-reviewer-1"
    assert reviewed["cases"][1]["label_review_status"] == "human_reviewed"
    assert "review_audit" not in json.loads(source_path.read_text(encoding="utf-8"))
    with pytest.raises(FileExistsError):
        write_reviewed_dataset(result, source_path, output_path)


def test_build_reviewed_dataset_refuses_incomplete_human_review(tmp_path):
    rows = [
        decision("bug-case", "bug-case-d1", "draft", ""),
        decision("clean-case", "", "draft", ""),
    ]
    csv_path, source_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, source_path)

    with pytest.raises(ReviewImportError, match="every case is fully reviewed"):
        write_reviewed_dataset(result, source_path, tmp_path / "reviewed.json")
    assert not (tmp_path / "reviewed.json").exists()


def test_reviewed_timestamp_requires_timezone(tmp_path):
    rows = [
        decision("bug-case", "bug-case-d1", "reviewed", "yes", **reviewed_metadata(reviewed_at="2026-10-24T11:30:00")),
        decision("clean-case", "", "reviewed", "no", **reviewed_metadata()),
    ]
    csv_path, dataset_path = fixture_data(tmp_path, rows)
    result = inspect_reviews(csv_path, dataset_path)
    assert any("ISO-8601 timestamp with timezone" in issue for issue in result["issues"])


def test_importer_raises_for_missing_csv_columns(tmp_path):
    csv_path = tmp_path / "incomplete.csv"
    csv_path.write_text("case_id,review_status\n", encoding="utf-8")
    with pytest.raises(ReviewImportError, match="missing required columns"):
        inspect_reviews(csv_path, DATASET_V2)


def test_review_packet_refuses_to_overwrite_candidate_csv(tmp_path):
    from evals.review_packet import main

    output = tmp_path / "review"
    output.mkdir()
    csv_path = output / "candidates.csv"
    csv_path.write_text("human decisions stay here\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--output", str(output)])
    assert exc.value.code == 2
    assert csv_path.read_text(encoding="utf-8") == "human decisions stay here\n"
