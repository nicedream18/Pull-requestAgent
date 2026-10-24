"""Unified-diff realism for the candidate dataset.

The earlier multi-file fixtures emitted every file as `@@ -0,0 +1,N @@` with only `+`
lines. That made three things untestable: a finding on unchanged context, a real
deletion, and a rename. These tests pin the fixed behaviour.
"""

from __future__ import annotations

import json


from evals.fixtures import DATASET_V2, _modified_file_hunk, case_files, snapshot_for
from prguard.verifier import changed_lines, verify_findings

CASES = {case["case_id"]: case for case in json.loads(DATASET_V2.read_text(encoding="utf-8"))["cases"]}


def test_modified_hunk_contains_context_and_deletions():
    base = "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n"
    head = "a = 1\nb = 2\nc = 30\nd = 4\ne = 5\n"
    hunk, additions, deletions, first_changed = _modified_file_hunk(base, head)
    assert additions == 1
    assert deletions == 1
    assert first_changed == 3
    assert hunk.startswith("@@ -1,5 +1,5 @@")
    assert "-c = 3" in hunk
    assert "+c = 30" in hunk
    # Context lines carry a single leading space, which is how a real diff marks them.
    assert "\n a = 1" in hunk


def test_head_line_numbers_are_used_not_base_numbers():
    """A deletion before the change shifts the head numbering."""
    base = "x = 0\ny = 1\nz = 2\n"
    head = "y = 1\nz = 20\n"
    hunk, additions, deletions, first_changed = _modified_file_hunk(base, head)
    # Deleting x and rewriting z is one addition and two deletions.
    assert (additions, deletions) == (1, 2)
    # `z = 20` is line 2 in the head revision, not line 3 in the base.
    assert first_changed == 2
    assert "@@ -1,3 +1,2 @@" in hunk


def test_real_diff_marks_only_the_changed_line():
    snapshot, _ = snapshot_for(CASES["real-diff-context-33"])
    touched = changed_lines(snapshot)
    assert touched["app/retry.py"] == {4}, "context lines must not count as changed"


def test_finding_on_unchanged_context_is_flagged_not_treated_as_changed():
    case = CASES["real-diff-context-33"]
    snapshot, sources = snapshot_for(case)
    lines = sources["app/retry.py"].splitlines()
    # Line 2 is context (`if times <= 0:`), not part of the change.
    assert "if times <= 0:" in lines[1]
    finding = _finding("app/retry.py", 2, "    if times <= 0:")
    verified, _ = verify_findings(
        [finding], snapshot, sources, check_conditions=False, resolve_paths=True, dedupe=False
    )
    assert verified
    assert verified[0].evidence_level.value == "partial"


def test_real_diff_carries_rename_metadata():
    case = CASES["real-diff-rename-34"]
    snapshot, _ = snapshot_for(case)
    only = snapshot.files[0]
    assert only.status == "renamed"
    assert only.previous_filename == "app/helpers.py"
    assert "rename from app/helpers.py" in snapshot.diff
    assert "rename to app/utilities.py" in snapshot.diff


def test_old_path_resolves_to_the_new_path_after_a_rename():
    case = CASES["real-diff-rename-34"]
    snapshot, sources = snapshot_for(case)
    finding = _finding("app/helpers.py", 2, "    handle = open(path)")
    verified, rejected = verify_findings(
        [finding], snapshot, sources, check_conditions=False, resolve_paths=True, dedupe=False
    )
    assert rejected == []
    assert verified[0].file_path == "app/utilities.py"
    assert verified[0].reported_path == "app/helpers.py"
    assert "renamed" in (verified[0].path_basis or "")


def test_deleted_line_is_not_present_in_the_head_source():
    case = CASES["real-diff-deletion-35"]
    snapshot, sources = snapshot_for(case)
    assert "-    os.chmod(path, 0o777)" in snapshot.diff
    assert "os.chmod" not in sources["app/uploads.py"]
    assert changed_lines(snapshot)["app/uploads.py"] == set(), "only deletions, no head lines changed"


def test_finding_quoting_a_deleted_line_is_rejected():
    case = CASES["real-diff-deletion-35"]
    snapshot, sources = snapshot_for(case)
    finding = _finding("app/uploads.py", 4, "    os.chmod(path, 0o777)")
    verified, rejected = verify_findings(
        [finding], snapshot, sources, check_conditions=False, resolve_paths=True, dedupe=False
    )
    assert verified == []
    assert rejected and rejected[0]["type"] in {"evidence_mismatch", "invalid_line"}


def test_removed_file_has_a_deletion_hunk_and_no_head_source():
    snapshot, sources = snapshot_for(CASES["mf-deleted-11"])
    removed = next(file for file in snapshot.files if file.status == "removed")
    assert removed.patch is None
    assert "deleted file mode" in snapshot.diff
    assert removed.path in sources  # the base text is still available for evidence checks


def test_synthetic_cases_are_marked_as_added_not_modified():
    """A file with no base revision cannot honestly claim to be `modified`."""
    snapshot, _ = snapshot_for(CASES["mf-two-defects-03"])
    assert all(file.status == "added" for file in snapshot.files)
    body = [line for line in snapshot.diff.splitlines() if not line.startswith("---")]
    assert not any(line.startswith("-") for line in body)


def test_additions_and_deletions_are_counted_per_file():
    snapshot, _ = snapshot_for(CASES["real-diff-context-33"])
    only = snapshot.files[0]
    assert (only.additions, only.deletions, only.changes) == (1, 1, 2)


def test_real_diff_cases_keep_their_base_code_in_the_dataset():
    """The generator must keep `base_code`, or a rebuild would silently lose the diff."""
    for case_id in ("real-diff-context-33", "real-diff-rename-34", "real-diff-deletion-35"):
        entries = case_files(CASES[case_id])
        assert any("base_code" in entry for entry in entries), case_id


def _finding(path: str, line: int, evidence: str):
    from prguard.models import Finding, FindingType, IssueType, Severity

    return Finding(
        category="bug",
        severity=Severity.MEDIUM,
        file_path=path,
        start_line=line,
        end_line=line,
        title="claim about this line",
        description="a claim used only to exercise the diff handling",
        evidence=evidence,
        suggestion="fix",
        confidence=0.8,
        issue_type=IssueType.RESOURCE_LEAK,
        finding_type=FindingType.DEFECT,
        trigger_condition="stated",
    )
