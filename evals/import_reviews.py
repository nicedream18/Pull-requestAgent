"""Validate and archive human decisions from ``docs/review/candidates.csv``.

This command never assigns a reviewer, review time, or approval status. ``--check`` is
read-only; ``--import`` writes a new immutable JSON snapshot and refuses to overwrite an
existing audit file.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.fixtures import DATASET_V2

IMPORTER_VERSION = "prguard-review-import-v1"
CANDIDATES_CSV = Path("docs/review/candidates.csv")
AUDIT_DIR = Path("docs/review/imports")
REPORT_PATH = Path("docs/review/progress.md")
ALLOWED_STATUSES = {"reviewed", "needs_revision", "indeterminate", "draft"}
ALLOWED_OPINIONS = {"yes", "no", "indeterminate"}
REQUIRED_COLUMNS = {
    "case_id",
    "split",
    "defect_id",
    "is_substantive_defect",
    "other_unlabelled_defects",
    "reviewer_notes",
    "review_status",
    "reviewer_id",
    "reviewed_at",
    "review_version",
}
AUDIT_COLUMNS = {"reviewer_id", "reviewed_at", "review_version"}


class ReviewImportError(ValueError):
    """The candidate review sheet is incomplete or inconsistent."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dataset_review_gate(dataset: dict[str, Any]) -> dict[str, Any]:
    """Return whether a candidate dataset is eligible for scored evaluation.

    Legacy development fixtures without ``label_policy`` remain runnable as pipeline
    regressions. Candidate datasets must carry a human-reviewed status for every case.
    """
    cases = dataset.get("cases", [])
    policy = dataset.get("label_policy")
    if policy is None:
        return {"required": False, "eligible": True, "pending_cases": [], "reason": "development fixture dataset"}
    pending = [
        str(case.get("case_id", "<missing-case-id>"))
        for case in cases
        if case.get("label_review_status") != "human_reviewed"
    ]
    declared_count = policy.get("human_reviewed_count")
    eligible = not pending and declared_count == len(cases) and bool(cases)
    return {
        "required": True,
        "eligible": eligible,
        "pending_cases": pending,
        "declared_human_reviewed_count": declared_count,
        "case_count": len(cases),
        "reason": "all candidate labels must be human-reviewed before scoring" if not eligible else "all candidate labels reviewed",
    }


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _parse_review_time(value: str, row_number: int, issues: list[str]) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
    except ValueError:
        issues.append(f"row {row_number}: reviewed_at must be an ISO-8601 timestamp with timezone")


def _defects_by_case(dataset: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    cases: dict[str, dict[str, dict[str, Any]]] = {}
    for case in dataset.get("cases", []):
        case_id = str(case.get("case_id", ""))
        if not case_id or case_id in cases:
            raise ReviewImportError(f"dataset contains a missing or duplicate case_id: {case_id!r}")
        defects = case.get("defects")
        if defects is None:
            defects = [case] if case.get("is_real_defect") else []
        by_id: dict[str, dict[str, Any]] = {}
        for defect in defects:
            defect_id = str(defect.get("defect_id", ""))
            if not defect_id or defect_id in by_id:
                raise ReviewImportError(f"dataset case {case_id} has a missing or duplicate defect_id")
            by_id[defect_id] = defect
        cases[case_id] = {"case": case, "defects": by_id}
    return cases


def inspect_reviews(csv_path: Path = CANDIDATES_CSV, dataset_path: Path = DATASET_V2) -> dict[str, Any]:
    """Read the CSV and return validated decisions plus a case-level progress summary."""
    csv_bytes = csv_path.read_bytes()
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    expected = _defects_by_case(dataset)
    issues: list[str] = []
    row_decisions: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    rows_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)

    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ReviewImportError(f"candidate CSV is missing required columns: {', '.join(missing)}")
        for row_number, raw in enumerate(reader, start=2):
            row = {key: (value or "").strip() for key, value in raw.items() if key is not None}
            case_id = row.get("case_id", "")
            defect_id = row.get("defect_id", "")
            status = row.get("review_status", "").lower()
            opinion = row.get("is_substantive_defect", "").lower()
            reviewer = row.get("reviewer_id", "")
            reviewed_at = row.get("reviewed_at", "")
            review_version = row.get("review_version", "")
            key = (case_id, defect_id)

            if case_id not in expected:
                issues.append(f"row {row_number}: unknown case_id {case_id!r}")
                continue
            known_defects = expected[case_id]["defects"]
            if defect_id and defect_id not in known_defects:
                issues.append(f"row {row_number}: defect_id {defect_id!r} does not belong to case {case_id!r}")
            if defect_id and defect_id in known_defects:
                truth = known_defects[defect_id]
                if row.get("split") != expected[case_id]["case"].get("split"):
                    issues.append(f"row {row_number}: split does not match dataset for {case_id}")
                if row.get("file_path") and row["file_path"] != truth.get("file_path"):
                    issues.append(f"row {row_number}: candidate file_path differs from dataset for {defect_id}")
            elif not known_defects and defect_id:
                issues.append(f"row {row_number}: clean case {case_id!r} cannot contain a defect_id")
            elif known_defects and not defect_id:
                issues.append(f"row {row_number}: case {case_id!r} requires a row for a labelled defect")

            if status not in ALLOWED_STATUSES:
                issues.append(f"row {row_number}: invalid review_status {status!r}")
            if opinion and opinion not in ALLOWED_OPINIONS:
                issues.append(f"row {row_number}: is_substantive_defect must be yes, no, or indeterminate")

            metadata = (reviewer, reviewed_at, review_version)
            metadata_count = sum(bool(value) for value in metadata)
            if status in {"reviewed", "needs_revision"} and metadata_count != len(AUDIT_COLUMNS):
                issues.append(f"row {row_number}: {status} requires reviewer_id, reviewed_at, and review_version")
            elif status == "indeterminate" and metadata_count not in {0, len(AUDIT_COLUMNS)}:
                issues.append(f"row {row_number}: indeterminate review metadata must be complete or entirely blank")
            elif status == "draft" and metadata_count:
                issues.append(f"row {row_number}: draft rows cannot claim reviewer audit metadata")
            if reviewed_at and metadata_count == len(AUDIT_COLUMNS):
                _parse_review_time(reviewed_at, row_number, issues)

            if status == "reviewed":
                if not opinion or opinion == "indeterminate":
                    issues.append(f"row {row_number}: reviewed requires a yes/no substantive-defect decision")
                expected_opinion = "yes" if defect_id else "no"
                if opinion and opinion != expected_opinion:
                    issues.append(
                        f"row {row_number}: reviewed opinion {opinion!r} conflicts with the candidate label; "
                        "use needs_revision until the ground truth is corrected"
                    )
                other = row.get("other_unlabelled_defects", "").lower()
                if other not in {"", "none", "no", "无", "n/a"}:
                    issues.append(f"row {row_number}: newly reported defects require needs_revision before evaluation")
            if status == "needs_revision" and not row.get("reviewer_notes"):
                issues.append(f"row {row_number}: needs_revision requires reviewer_notes")
            if status == "needs_revision" and opinion not in ALLOWED_OPINIONS:
                issues.append(f"row {row_number}: needs_revision requires a substantive-defect opinion")
            if status == "indeterminate" and metadata_count and (opinion != "indeterminate" or not row.get("reviewer_notes")):
                issues.append(f"row {row_number}: an audited indeterminate decision requires an indeterminate opinion and reviewer_notes")

            decision = {
                "row_number": row_number,
                "case_id": case_id,
                "split": row.get("split", ""),
                "defect_id": defect_id or None,
                "proposed_file_path": row.get("file_path", "") or None,
                "proposed_line_range": row.get("line_range", "") or None,
                "proposed_issue_type": row.get("issue_type", "") or None,
                "proposed_severity": row.get("severity", "") or None,
                "is_substantive_defect": opinion or None,
                "other_unlabelled_defects": row.get("other_unlabelled_defects", "") or None,
                "reviewer_notes": row.get("reviewer_notes", "") or None,
                "review_status": status,
                "reviewer_id": reviewer or None,
                "reviewed_at": reviewed_at or None,
                "review_version": review_version or None,
                "legacy_unattributed_status": status == "indeterminate" and metadata_count == 0,
            }
            previous = seen.get(key)
            if previous is not None:
                fields = ("review_status", "is_substantive_defect", "reviewer_id", "reviewed_at", "review_version", "reviewer_notes")
                conflict = [field for field in fields if previous.get(field) != decision.get(field)]
                if conflict:
                    issues.append(
                        f"row {row_number}: conflicting decisions for {case_id}/{defect_id or '<clean-case>'}: "
                        + ", ".join(conflict)
                    )
                else:
                    issues.append(f"row {row_number}: duplicate review row for {case_id}/{defect_id or '<clean-case>'}")
            else:
                seen[key] = decision
                row_decisions.append(decision)
                rows_by_case[case_id].append(decision)

    for case_id, entry in expected.items():
        expected_ids = set(entry["defects"])
        observed_ids = {row["defect_id"] for row in rows_by_case.get(case_id, []) if row["defect_id"]}
        has_clean_row = any(row["defect_id"] is None for row in rows_by_case.get(case_id, []))
        missing_ids = expected_ids - observed_ids
        if missing_ids:
            issues.append(f"case {case_id}: missing candidate rows for defect_id(s): {', '.join(sorted(missing_ids))}")
        if not expected_ids and not has_clean_row:
            issues.append(f"case {case_id}: clean case has no review row and cannot be silently omitted")
        if expected_ids and has_clean_row:
            issues.append(f"case {case_id}: labelled-defect case cannot also have a clean-case row")

    case_decisions = []
    case_status_counts: Counter[str] = Counter()
    for case_id, entry in expected.items():
        case_rows = rows_by_case.get(case_id, [])
        statuses = {row["review_status"] for row in case_rows}
        if statuses == {"reviewed"} and len(case_rows) == max(1, len(entry["defects"])):
            case_status = "reviewed"
        elif "needs_revision" in statuses:
            case_status = "needs_revision"
        elif "indeterminate" in statuses:
            case_status = "indeterminate"
        else:
            case_status = "draft"
        case_status_counts[case_status] += 1
        case_decisions.append({
            "case_id": case_id,
            "split": entry["case"].get("split"),
            "labelled_defect_count": len(entry["defects"]),
            "review_status": case_status,
        })

    row_status_counts = Counter(row["review_status"] for row in row_decisions)
    clean_cases = [entry for entry in expected.values() if not entry["defects"]]
    reviewed_clean = sum(
        1 for case in case_decisions if case["review_status"] == "reviewed" and not expected[case["case_id"]]["defects"]
    )
    reviewed_cases = case_status_counts["reviewed"]
    summary = {
        "case_count": len(expected),
        "decision_row_count": len(row_decisions),
        "reviewed_cases": reviewed_cases,
        "needs_revision_cases": case_status_counts["needs_revision"],
        "indeterminate_cases": case_status_counts["indeterminate"],
        "draft_cases": case_status_counts["draft"],
        "cases_needing_human_work": len(expected) - reviewed_cases,
        "clean_cases": len(clean_cases),
        "reviewed_clean_cases": reviewed_clean,
        "clean_cases_needing_review": len(clean_cases) - reviewed_clean,
        "row_status_counts": {status: row_status_counts[status] for status in sorted(ALLOWED_STATUSES)},
        "ready_for_freeze": not issues and reviewed_cases == len(expected),
    }
    return {
        "summary": summary,
        "case_decisions": case_decisions,
        "decisions": row_decisions,
        "issues": issues,
        "source_csv_sha256": _sha256(csv_bytes),
        "dataset_sha256": _sha256(dataset_bytes),
        "dataset_version": dataset.get("dataset_version", "unknown"),
    }


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def render_progress(result: dict[str, Any], csv_path: Path, dataset_path: Path) -> str:
    summary = result["summary"]
    counts = summary["row_status_counts"]
    lines = [
        "# 人工审核进度",
        "",
        f"候选集：`{_display_path(dataset_path)}`（{result['dataset_version']}），审核表：`{_display_path(csv_path)}`。",
        "",
        f"- 案例：{summary['case_count']}；已完整审核：{summary['reviewed_cases']}；仍需人工处理：{summary['cases_needing_human_work']}。",
        f"- 案例状态：draft {summary['draft_cases']}，needs_revision {summary['needs_revision_cases']}，indeterminate {summary['indeterminate_cases']}。",
        f"- 审核行状态：reviewed {counts['reviewed']}，needs_revision {counts['needs_revision']}，indeterminate {counts['indeterminate']}，draft {counts['draft']}。",
        f"- 无缺陷案例：{summary['reviewed_clean_cases']}/{summary['clean_cases']} 已审核；仍待确认 {summary['clean_cases_needing_review']}。",
        f"- 可冻结：{'是' if summary['ready_for_freeze'] else '否'}。未审核、需修订或无法判断的案例不进入正式指标。",
        "",
        "审核身份、审核时间和审核版本必须由审核者填写；`indeterminate` 若没有这三项，仅作为候选表中的初始状态，不构成已记录的人审决定。",
    ]
    if result["issues"]:
        lines.extend(["", "## 校验问题", ""])
        lines.extend(f"- {issue}" for issue in result["issues"])
    return "\n".join(lines) + "\n"


def _new_audit_path(directory: Path, imported_at: datetime) -> Path:
    stamp = imported_at.strftime("%Y%m%dT%H%M%S%fZ")
    return directory / f"review-import-{stamp}.json"


def write_audit_snapshot(result: dict[str, Any], path: Path) -> None:
    """Create an immutable import snapshot; fail if the destination already exists."""
    payload = {
        "schema_version": "1.0",
        "importer_version": IMPORTER_VERSION,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "dataset_version": result["dataset_version"],
        "dataset_sha256": result["dataset_sha256"],
        "source_csv_sha256": result["source_csv_sha256"],
        "summary": result["summary"],
        "case_decisions": result["case_decisions"],
        "decisions": result["decisions"],
        "validation_issues": result["issues"],
        "note": "Import timestamp is system-generated; reviewer identity, review time, version, status and opinion are copied from the human-edited CSV.",
    }
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_reviewed_dataset(result: dict[str, Any], dataset_path: Path, output_path: Path) -> None:
    """Write a new scored-dataset version from a complete, validated human review."""
    if not result["summary"]["ready_for_freeze"] or result["issues"]:
        raise ReviewImportError("cannot build a reviewed dataset until every case is fully reviewed")
    if _sha256(dataset_path.read_bytes()) != result["dataset_sha256"]:
        raise ReviewImportError("source dataset changed after review validation; regenerate the review packet")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite reviewed dataset: {output_path}")

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    decisions_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in result["decisions"]:
        decisions_by_case[decision["case_id"]].append(decision)

    for case in dataset.get("cases", []):
        case_id = str(case["case_id"])
        decisions = decisions_by_case.get(case_id, [])
        if not decisions or any(item["review_status"] != "reviewed" for item in decisions):
            raise ReviewImportError(f"case {case_id} does not have a complete human review")
        case["label_review_status"] = "human_reviewed"
        case["label_review"] = decisions

    csv_sha = result["source_csv_sha256"]
    dataset["dataset_version"] = f"{result['dataset_version']}-reviewed-{csv_sha[:8]}"
    policy = dict(dataset.get("label_policy") or {})
    policy.update({
        "allowed_status": sorted(ALLOWED_STATUSES),
        "current": "all cases, including clean cases, have a validated human review",
        "human_reviewed_count": result["summary"]["case_count"],
        "llm_generated_labels": False,
        "review_csv_sha256": csv_sha,
    })
    dataset["label_policy"] = policy
    dataset["review_audit"] = {
        "source_dataset_version": result["dataset_version"],
        "source_dataset_sha256": result["dataset_sha256"],
        "source_csv_sha256": csv_sha,
        "importer_version": IMPORTER_VERSION,
        "decisions": result["decisions"],
        "case_decisions": result["case_decisions"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        json.dump(dataset, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and archive human candidate-label review decisions.")
    parser.add_argument("--csv", type=Path, default=CANDIDATES_CSV)
    parser.add_argument("--dataset", type=Path, default=DATASET_V2)
    parser.add_argument("--import", dest="do_import", action="store_true", help="Write a new immutable audit snapshot.")
    parser.add_argument("--audit-dir", type=Path, default=AUDIT_DIR)
    parser.add_argument("--report", type=Path, default=None, help="Optionally write a current progress report.")
    parser.add_argument("--require-reviewed", action="store_true", help="Return non-zero unless all cases are fully reviewed.")
    parser.add_argument(
        "--build-reviewed-dataset",
        type=Path,
        default=None,
        help="Write a new dataset file from a complete human review; never overwrites an existing file.",
    )
    args = parser.parse_args(argv)

    try:
        result = inspect_reviews(args.csv, args.dataset)
    except (OSError, json.JSONDecodeError, ReviewImportError) as exc:
        parser.error(str(exc))

    summary = result["summary"]
    print(
        f"cases {summary['case_count']} | reviewed {summary['reviewed_cases']} | "
        f"needs_revision {summary['needs_revision_cases']} | indeterminate {summary['indeterminate_cases']} | "
        f"draft {summary['draft_cases']} | human work remaining {summary['cases_needing_human_work']}"
    )
    print(
        f"clean cases reviewed {summary['reviewed_clean_cases']}/{summary['clean_cases']} | "
        f"ready_for_freeze={str(summary['ready_for_freeze']).lower()}"
    )
    for issue in result["issues"]:
        print(f"ERROR: {issue}")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_progress(result, args.csv, args.dataset), encoding="utf-8")
        print(f"wrote progress report: {args.report}")

    if result["issues"]:
        return 2
    if args.require_reviewed and not summary["ready_for_freeze"]:
        print("formal benchmark gate: blocked until every case, including clean cases, is reviewed")
        return 2
    if args.build_reviewed_dataset:
        try:
            write_reviewed_dataset(result, args.dataset, args.build_reviewed_dataset)
        except (OSError, json.JSONDecodeError, ReviewImportError) as exc:
            parser.error(str(exc))
        print(f"wrote new human-reviewed dataset: {args.build_reviewed_dataset}")
    if args.do_import:
        args.audit_dir.mkdir(parents=True, exist_ok=True)
        path = _new_audit_path(args.audit_dir, datetime.now(timezone.utc))
        write_audit_snapshot(result, path)
        print(f"wrote immutable audit snapshot: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
