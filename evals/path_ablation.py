"""Offline ablation over one frozen set of real model findings.

No model is called. The frozen `raw_findings` from a recorded run are replayed through
four configurations so each difference is attributable to one change:

    A  layer 1 only                     (no path recovery, no dedup)
    B  + path recovery
    C  + duplicate merging
    D  + layer 2 evidence checks

Ground truth is used only to *score* the outcome, never to decide a resolution.

Usage:

    PYTHONPATH=src .venv/bin/python -m evals.path_ablation \
        --input artifacts/evals/ablation/20261024T102247Z/ablation.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from evals.fixtures import snapshot_for
from evals.metrics import EVALUATOR_VERSION, evaluate_cases, strict_match
from prguard.models import Finding, FindingType
from prguard.verifier import verify_findings

FROZEN = Path(__file__).parent / "frozen" / "online_20261024T102247Z.json"

CONFIGS = [
    ("A", "layer 1 only", {"resolve_paths": False, "dedupe": False, "check_conditions": False}),
    ("B", "+ path recovery", {"resolve_paths": True, "dedupe": False, "check_conditions": False}),
    ("C", "+ dedup", {"resolve_paths": True, "dedupe": True, "check_conditions": False}),
    ("D", "+ layer 2", {"resolve_paths": True, "dedupe": True, "check_conditions": True}),
]


def load_frozen(path: Path) -> tuple[dict, list[dict]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    dataset_path = Path(document.get("dataset") or "evals/datasets/prguard_v1.json")
    if not dataset_path.exists():
        dataset_path = Path("evals/datasets/prguard_v1.json")
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))["cases"]
    return document, cases


def replay(document: dict, cases: list[dict], options: dict) -> tuple[dict, list[dict], list[dict], dict]:
    by_case: dict[str, list[Finding]] = {}
    resolutions: list[dict] = []
    problems: list[dict] = []
    rejections: Counter[str] = Counter()

    for case in cases:
        case_id = case["case_id"]
        snapshot, sources = snapshot_for(case)
        raw = [Finding(**entry) for entry in document["raw_findings"].get(case_id, [])]
        verified, rejected = verify_findings(
            raw,
            snapshot,
            sources,
            check_conditions=options["check_conditions"],
            resolve_paths=options["resolve_paths"],
            dedupe=options["dedupe"],
        )
        by_case[case_id] = verified
        for entry in rejected:
            rejections[str(entry.get("type", "unknown"))] += 1
            problems.append({"case_id": case_id, "type": entry.get("type"), "message": entry.get("message")})
        for finding in verified:
            if finding.reported_path is not None and finding.reported_path != finding.file_path:
                resolutions.append({
                    "case_id": case_id,
                    "original": finding.reported_path,
                    "resolved": finding.file_path,
                    "status": finding.path_status,
                    "basis": finding.path_basis,
                    "issue_type": finding.issue_type.value,
                    "agent": finding.source_agent,
                })

    scores = evaluate_cases(cases, by_case)
    return scores, resolutions, [{"rejections": dict(rejections), "problems": problems}], by_case


def per_case_table(cases: list[dict], by_case: dict[str, list[Finding]]) -> list[dict]:
    rows = []
    for case in cases:
        findings = by_case.get(case["case_id"], [])
        defects = [f for f in findings if f.finding_type is FindingType.DEFECT]
        hits = [f for f in defects if strict_match(f, case) is not None]
        rows.append({
            "case_id": case["case_id"],
            "is_real_defect": case["is_real_defect"],
            "findings": len(findings),
            "defects": len(defects),
            "tp": len(hits) if case["is_real_defect"] else 0,
            "fp": len(defects) - (len(hits) if case["is_real_defect"] else 0),
            "fn": (1 - len(hits)) if case["is_real_defect"] else 0,
            "titles": [f.title[:48] for f in defects],
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay frozen findings through path/dedup ablations.")
    parser.add_argument(
        "--input",
        type=Path,
        default=FROZEN,
        help="Frozen run to replay. Defaults to the committed fixture so CI never skips.",
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/evals/path-ablation"))
    args = parser.parse_args(argv)

    document, cases = load_frozen(args.input)
    total_raw = sum(len(v) for v in document["raw_findings"].values())

    rows = []
    details: dict[str, dict] = {}
    for tag, label, options in CONFIGS:
        scores, resolutions, extra, by_case = replay(document, cases, options)
        valid_paths = 0
        for case in cases:
            snapshot, _ = snapshot_for(case)
            paths = {f.path for f in snapshot.files}
            valid_paths += sum(
                1 for entry in document["raw_findings"].get(case["case_id"], []) if entry["file_path"] in paths
            )
        rows.append({
            "tag": tag,
            "label": label,
            "options": options,
            "precision": scores["defect_precision"],
            "recall": scores["defect_recall"],
            "f1": scores["f1_score"],
            "strict_f1": scores["strict_f1"],
            "tp": scores["tp"],
            "fp": scores["fp"],
            "fn": scores["fn"],
            "advisory": scores["advisory_count"],
            "needs_review": scores["needs_human_review_count"],
            "findings": scores["advisory_count"] + scores["tp"] + scores["fp"],
            "duplicates_merged": sum(
                1 for case in cases for _ in ()
            ),
            "recoveries": len(resolutions),
            "recoveries_by_status": dict(Counter(item["status"] for item in resolutions)),
            "valid_path_rate": valid_paths / max(1, total_raw),
            "rejections": extra[0]["rejections"],
        })
        details[tag] = {
            "per_case": per_case_table(cases, by_case),
            "resolutions": resolutions,
            "problems": extra[0]["problems"],
        }

    print(f"frozen findings: {total_raw} | evaluator: {EVALUATOR_VERSION}")
    print(f"{'':<3}{'config':<20}{'P':>7}{'R':>7}{'F1':>7}{'Strict':>8}{'TP':>5}{'FP':>5}{'FN':>5}{'adv':>5}{'recov':>7}")
    for row in rows:
        print(
            f"{row['tag']:<3}{row['label']:<20}{row['precision']:>7.3f}{row['recall']:>7.3f}"
            f"{row['f1']:>7.3f}{row['strict_f1']:>8.3f}{row['tp']:>5}{row['fp']:>5}{row['fn']:>5}"
            f"{row['advisory']:>5}{row['recoveries']:>7}"
        )

    print("\nrecovery statuses by configuration:")
    for row in rows:
        print(f"  {row['tag']}: {row['recoveries_by_status']}")

    print("\nrejection reasons by configuration:")
    for row in rows:
        print(f"  {row['tag']}: {row['rejections']}")

    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / f"path-ablation-{args.input.parent.name}.json"
    destination.write_text(
        json.dumps(
            {
                "evaluator_version": EVALUATOR_VERSION,
                "source": str(args.input),
                "raw_finding_count": total_raw,
                "rows": rows,
                "resolutions": details,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nwrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
