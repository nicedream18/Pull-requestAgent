"""Consistency check for the candidate dataset's draft labels.

This does **not** create labels. It checks that each hand-written label is at least
internally consistent with the code it points at, so a reviewer's time goes to judgement
rather than to typos:

* the cited file exists in the case;
* the cited line range is inside that file;
* `supporting_evidence` appears verbatim in the cited lines (or within two) of that file;
* the deterministic condition check does not actively *contradict* the claimed issue type.

A `contradicted` verdict means either the label is wrong or the fixture is a deliberate
trap for the pipeline. Traps are expected here, so this reports rather than fails, and
`--strict` turns contradictions into an error for the reviewed subset only.

Run:

    PYTHONPATH=src python -m evals.check_labels
    PYTHONPATH=src python -m evals.check_labels --strict --require-reviewed
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from evals.fixtures import DATASET_V2, case_files
from prguard.evidence import CONTRADICTED, UNKNOWN, evaluate_condition
from prguard.models import IssueType

LINE_SLACK = 2


def check(dataset: dict) -> list[dict]:
    problems: list[dict] = []
    for case in dataset["cases"]:
        files = {entry["path"]: entry["code"] for entry in case_files(case)}
        for entry in case["defects"]:
            path = entry["file_path"]
            source = files.get(path)
            if source is None:
                problems.append({"case": case["case_id"], "defect": entry["defect_id"], "problem": "file_not_in_case"})
                continue
            lines = source.splitlines()
            start, end = entry["line_range"]
            if start < 1 or end > len(lines):
                problems.append({"case": case["case_id"], "defect": entry["defect_id"], "problem": "line_out_of_range"})
                continue

            evidence = entry["supporting_evidence"].strip()
            window = "\n".join(lines[max(0, start - 1 - LINE_SLACK):min(len(lines), end + LINE_SLACK)])
            if evidence not in window:
                problems.append(
                    {"case": case["case_id"], "defect": entry["defect_id"], "problem": "evidence_not_at_line"}
                )

            try:
                issue = IssueType(entry["issue_type"])
            except ValueError:
                problems.append(
                    {"case": case["case_id"], "defect": entry["defect_id"], "problem": "unknown_issue_type"}
                )
                continue
            verdict = evaluate_condition(issue, source, start)
            if verdict.status == CONTRADICTED:
                problems.append({
                    "case": case["case_id"],
                    "defect": entry["defect_id"],
                    "problem": "condition_contradicted",
                    "reason": verdict.reason,
                })
            elif verdict.status == UNKNOWN:
                problems.append({
                    "case": case["case_id"],
                    "defect": entry["defect_id"],
                    "problem": "condition_unknown",
                    "reason": verdict.reason,
                })
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check candidate labels for internal consistency.")
    parser.add_argument("--dataset", type=Path, default=DATASET_V2)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on contradictions.")
    parser.add_argument(
        "--require-reviewed",
        action="store_true",
        help="Refuse to proceed unless every case is human_reviewed.",
    )
    args = parser.parse_args(argv)

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    reviewed = sum(1 for case in dataset["cases"] if case["label_review_status"] == "human_reviewed")
    total = len(dataset["cases"])
    print(f"dataset: {args.dataset}")
    print(f"cases: {total} | human_reviewed: {reviewed} | draft: {total - reviewed}")

    if args.require_reviewed and reviewed < total:
        print("refusing: not every case is human_reviewed")
        return 2

    problems = check(dataset)
    counts = Counter(problem["problem"] for problem in problems)
    print(f"labelled defects: {sum(len(case['defects']) for case in dataset['cases'])}")
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count}")
    for problem in problems:
        reason = f" | {problem['reason'][:70]}" if problem.get("reason") else ""
        print(f"  - {problem['case']}/{problem['defect']}: {problem['problem']}{reason}")

    contradictions = counts.get("condition_contradicted", 0)
    if args.strict and contradictions:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
