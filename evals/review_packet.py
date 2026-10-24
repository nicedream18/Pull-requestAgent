"""Generate the human review packet for the candidate dataset.

This produces material for a **human** reviewer. It never marks anything as reviewed, and
none of its output may be treated as a second opinion: it can show that a label is
internally consistent, not that it is correct.

Run:

    PYTHONPATH=src python -m evals.review_packet
    PYTHONPATH=src python -m evals.review_packet --split test

Writes:
    <output>/candidates.md    one section per case, for reading
    <output>/candidates.csv   one row per defect, for triage
    <output>/summary.md       what still needs a decision

The default output is ``docs/review``. Existing review CSVs are never overwritten;
choose a new output directory to regenerate a clean packet.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from evals.fixtures import DATASET_V2, case_defects, case_files
from prguard.evidence import CONTRADICTED, UNKNOWN, evaluate_condition
from prguard.models import IssueType

OUT_DIR = Path("docs/review")

# The only statuses a human may set. `draft` is where everything starts.
STATUSES = ("draft", "reviewed", "needs_revision", "indeterminate")


def _evidence_line(source: str, evidence: str) -> int | None:
    needle = evidence.strip().splitlines()[0].strip() if evidence.strip() else ""
    if not needle:
        return None
    hits = [index + 1 for index, line in enumerate(source.splitlines()) if needle in line]
    return hits[0] if len(hits) == 1 else None


def _condition_note(issue_type: str, source: str, line: int) -> str:
    try:
        verdict = evaluate_condition(IssueType(issue_type), source, line)
    except ValueError:
        return "no deterministic check for this issue type"
    if verdict.status == CONTRADICTED:
        return f"CONTRADICTED by the checker: {verdict.reason}"
    if verdict.status == UNKNOWN:
        return f"UNKNOWN: {verdict.reason}"
    return f"supported: {verdict.reason}"


def _code_block(text: str) -> str:
    return "```python\n" + text.rstrip("\n") + "\n```"


def build(dataset: dict, split: str | None) -> tuple[str, list[dict], str]:
    cases = [case for case in dataset["cases"] if split is None or case["split"] == split]
    rows: list[dict] = []
    sections: list[str] = []
    pending: list[str] = []

    header = [
        "# Candidate label review packet",
        "",
        f"Dataset: `{dataset['dataset_version']}`, {len(cases)} case(s)"
        + (f" from the `{split}` split" if split else ""),
        "",
        "**Every label below is `draft`.** This packet is input for a human reviewer. It was",
        "produced mechanically from the fixture code; it is not a second opinion and it does not",
        "raise any label's status. Allowed statuses: " + ", ".join(f"`{s}`" for s in STATUSES) + ".",
        "",
        "For each case, decide:",
        "",
        "1. Is the labelled defect real, and is the severity defensible?",
        "2. Is the line range the smallest accurate range?",
        "3. Is there any *other* real defect that is not labelled? (This is the main risk for",
        "   the clean cases.)",
        "4. Is `indeterminate` the honest answer because the code alone cannot decide?",
        "",
        "Record decisions in `candidates.csv`; do not edit `prguard_v2.json` directly.",
        "",
        "---",
        "",
    ]

    for case in cases:
        files = {entry["path"]: entry for entry in case_files(case)}
        defects = case_defects(case)
        status = case["label_review_status"]
        pending.append(f"{case['case_id']} ({status})")

        sections.append(f"## {case['case_id']}")
        sections.append("")
        sections.append(f"**PR title:** {case['pr_title']}")
        sections.append("")
        sections.append(
            f"**Split:** `{case['split']}` | **Label status:** `{status}` | "
            f"**Labelled defects:** {len(defects)} | **Files:** {len(files)}"
        )
        sections.append("")

        if not defects:
            sections.append("**No defect is labelled.** Confirm the change is genuinely clean.")
            sections.append("")

        for path, entry in files.items():
            sections.append(f"### `{path}`" + (f" ({entry['status']})" if entry.get("status") else ""))
            sections.append("")
            if "base_code" in entry:
                sections.append("**Before (base revision):**")
                sections.append("")
                sections.append(_code_block(entry["base_code"]))
                sections.append("**After (head revision):**")
                sections.append("")
                sections.append(_code_block(entry["code"]))
            else:
                sections.append("**Content (no base revision in this case):**")
                sections.append("")
                sections.append(_code_block(entry["code"]))

        if defects:
            sections.append("### Proposed ground truth")
            sections.append("")
            for entry in defects:
                source = files.get(entry["file_path"], {}).get("code", "")
                actual_line = _evidence_line(source, entry["supporting_evidence"])
                start, end = entry["line_range"]
                sections.append(f"#### `{entry['defect_id']}`")
                sections.append("")
                sections.append("| Field | Value |")
                sections.append("| --- | --- |")
                sections.append(f"| issue_type | `{entry['issue_type']}` |")
                sections.append(f"| severity | `{entry['severity']}` |")
                sections.append(f"| file | `{entry['file_path']}` |")
                sections.append(f"| line_range | {start}-{end} |")
                sections.append(f"| evidence line in file | {actual_line if actual_line else 'not uniquely located'} |")
                sections.append(f"| deterministic check | {_condition_note(entry['issue_type'], source, start)} |")
                sections.append("")
                sections.append(f"- **Description:** {entry['description']}")
                sections.append(f"- **Trigger condition:** {entry['trigger_condition']}")
                sections.append(f"- **Expected:** {entry['expected_behavior']}")
                sections.append(f"- **Actual:** {entry['actual_behavior']}")
                sections.append(f"- **Evidence:** `{entry['supporting_evidence']}`")
                sections.append("")
                rows.append({
                    "case_id": case["case_id"],
                    "split": case["split"],
                    "defect_id": entry["defect_id"],
                    "file_path": entry["file_path"],
                    "line_range": f"{start}-{end}",
                    "issue_type": entry["issue_type"],
                    "severity": entry["severity"],
                    "evidence": entry["supporting_evidence"],
                    "trigger_condition": entry["trigger_condition"],
                    "is_substantive_defect": "",
                    "other_unlabelled_defects": "",
                    "reviewer_notes": "",
                    "review_status": status,
                    "reviewer_id": "",
                    "reviewed_at": "",
                    "review_version": "",
                })

        if not defects:
            rows.append({
                "case_id": case["case_id"],
                "split": case["split"],
                "defect_id": "",
                "file_path": ", ".join(files),
                "line_range": "",
                "issue_type": "",
                "severity": "",
                "evidence": "",
                "trigger_condition": "",
                "is_substantive_defect": "",
                "other_unlabelled_defects": "",
                "reviewer_notes": "",
                "review_status": status,
                "reviewer_id": "",
                "reviewed_at": "",
                "review_version": "",
            })

        if case.get("label_notes"):
            sections.append("### Author notes")
            sections.append("")
            sections.append(case["label_notes"])
            sections.append("")
        sections.append("---")
        sections.append("")

    summary = [
        "# Review status",
        "",
        f"Cases: {len(cases)} | draft: {sum(1 for c in cases if c['label_review_status'] == 'draft')} | "
        f"reviewed: {sum(1 for c in cases if c['label_review_status'] == 'reviewed')} | "
        f"needs_revision: {sum(1 for c in cases if c['label_review_status'] == 'needs_revision')} | "
        f"indeterminate: {sum(1 for c in cases if c['label_review_status'] == 'indeterminate')}",
        "",
        "No label may become `reviewed` until a named human reviewer signs the row in",
        "`candidates.csv`. This file is generated; regenerate it after the CSV is updated.",
        "",
        "## Clean cases — check these hardest",
        "",
        "A missed defect in a clean case inflates measured precision silently.",
        "",
    ]
    clean = [c["case_id"] for c in cases if not case_defects(c)]
    summary.extend(f"- `{name}`" for name in clean) if clean else summary.append("- none")
    summary.extend(["", "## Pending", ""])
    summary.extend(f"- `{item}`" for item in pending)
    summary.append("")
    return "\n".join(header + sections), rows, "\n".join(summary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the human review packet.")
    parser.add_argument("--dataset", type=Path, default=DATASET_V2)
    parser.add_argument("--split", choices=("dev", "test", "holdout"), default=None)
    parser.add_argument("--output", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    if (args.output / "candidates.csv").exists():
        parser.error(
            f"refusing to overwrite {args.output / 'candidates.csv'}; "
            "choose a new --output directory to preserve reviewer decisions"
        )

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    markdown, rows, summary = build(dataset, args.split)

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "candidates.md").write_text(markdown + "\n", encoding="utf-8")
    (args.output / "summary.md").write_text(summary + "\n", encoding="utf-8")
    with (args.output / "candidates.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    print(f"cases: {len({row['case_id'] for row in rows})} | rows: {len(rows)}")
    print(f"wrote {args.output/'candidates.md'}, {args.output/'candidates.csv'}, {args.output/'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
