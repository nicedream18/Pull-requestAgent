"""Re-score a recorded PRGuard run without regenerating model output.

The first online run (prompt ``prguard-review-v1``) was scored by an older, looser
matcher. This module reads that recorded JSON, maps each finding onto the current
``Finding`` schema with a *deterministic, documented* mapping, and re-scores it.

Rules for the mapping, in order of importance:

1. Nothing is inferred from the ground truth. In particular ``issue_type`` is never
   filled in from the labelled ``defect_type``, and a missing ``start_line`` is
   never filled in from the labelled ``line_range``. Doing either would leak the
   answer into the prediction.
2. ``issue_type`` becomes ``other`` for every v1 finding, because v1 had no
   classification field. The unified matcher then has to fall back to its text
   channel, which is exactly the honest situation for that run.
3. ``finding_type`` becomes ``defect`` for every v1 finding, because v1 had no
   advisory class. This is the least favourable assumption for the new pipeline and
   the most favourable one for v1.
4. Any field already present is copied verbatim.

Usage:

    PYTHONPATH=src .venv/bin/python -m evals.replay_v1 \
        --input artifacts/evals/online/20261024T081509Z/results.json \
        --dataset evals/datasets/prguard_v1.json \
        --output artifacts/evals/replay
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.metrics import EVALUATOR_VERSION, evaluate_cases
from prguard.models import Finding, FindingType, IssueType

MAPPING = {
    "issue_type": "always IssueType.OTHER: prompt prguard-review-v1 emitted no classification",
    "finding_type": "always FindingType.DEFECT: prompt prguard-review-v1 had no advisory class",
    "start_line": "copied verbatim, or left None; never filled from the labelled line_range",
    "evidence": "copied verbatim; used by the unified matcher to prove the claim points at real code",
    "trigger_condition": "left empty: v1 output has no separate trigger statement",
}


def to_finding(raw: dict) -> Finding:
    """Deterministic schema mapping for one recorded v1 finding."""
    payload = {
        "category": raw.get("category", "other"),
        "severity": raw.get("severity", "medium"),
        "file_path": raw["file_path"],
        "start_line": raw.get("start_line"),
        "end_line": raw.get("end_line"),
        "title": raw.get("title", "untitled"),
        "description": raw.get("description", ""),
        "evidence": raw.get("evidence", ""),
        "suggestion": raw.get("suggestion", ""),
        "confidence": float(raw.get("confidence", 0.5)),
        "source_agent": raw.get("source_agent", ""),
        "issue_type": IssueType.OTHER,
        "finding_type": FindingType.DEFECT,
    }
    if not payload["evidence"].strip():
        # Finding requires at least three characters; keep the row but make the
        # absence explicit rather than inventing text.
        payload["evidence"] = "(no evidence recorded by prompt v1)"
    if len(payload["description"]) < 12:
        payload["description"] = (payload["description"] + " (recorded v1 description)").strip()
    if len(payload["title"]) < 4:
        payload["title"] = "v1 finding"
    if len(payload["suggestion"]) < 3:
        payload["suggestion"] = "(none recorded)"
    return Finding(**payload)


def _load(document: dict) -> tuple[dict[str, list[Finding]], int]:
    by_case: dict[str, list[Finding]] = {}
    unmapped = 0
    for strategy, payload in document["cases"].items():
        if strategy != "dynamic":
            continue
        for record in payload:
            findings = []
            for raw in record.get("findings", []):
                try:
                    findings.append(to_finding(raw))
                except Exception:
                    unmapped += 1
            by_case[record["case_id"]] = findings
    return by_case, unmapped


def _reverify(cases: list[dict], by_case: dict[str, list[Finding]], *, check_conditions: bool):
    """Push the recorded findings through the current verifier, offline.

    ``require_trigger=False`` is deliberate: prompt v1 could not emit a trigger
    condition, and charging the verifier for a prompt limitation would mix two
    variables. With it disabled, this isolates the evidence checks only.
    """
    from evals.fixtures import snapshot_for
    from prguard.verifier import verify_findings

    verified_by_case: dict[str, list[Finding]] = {}
    rejected = 0
    for case in cases:
        findings = by_case.get(case["case_id"], [])
        snapshot, sources = snapshot_for(case)
        verified, rejects = verify_findings(
            [item.model_copy() for item in findings],
            snapshot,
            sources,
            check_conditions=check_conditions,
            require_trigger=False,
            # Keep this historical replay about verifier acceptance only. Path
            # recovery and the new deduplicator are measured separately by
            # evals.path_ablation, and mixing them here would move the historical
            # numbers every time dedup changes.
            resolve_paths=False,
            dedupe=False,
        )
        verified_by_case[case["case_id"]] = verified
        rejected += len(rejects)
    return verified_by_case, rejected


def replay(document: dict, dataset: dict) -> dict:
    cases = dataset["cases"]
    by_case, unmapped = _load(document)

    raw_scores = evaluate_cases(cases, by_case)

    layer1_by_case, layer1_rejected = _reverify(cases, by_case, check_conditions=False)
    layer1_scores = evaluate_cases(cases, layer1_by_case)

    both_by_case, both_rejected = _reverify(cases, by_case, check_conditions=True)
    both_scores = evaluate_cases(cases, both_by_case)

    def strip(scores):
        return {key: value for key, value in scores.items() if key != "details"}

    return {
        "evaluator_version": EVALUATOR_VERSION,
        "source": "recorded prguard-review-v1 output, re-scored; no model was called",
        "mapping": MAPPING,
        "unmapped_findings": unmapped,
        "raw": strip(raw_scores),
        "after_layer1": {**strip(layer1_scores), "rejected_findings": layer1_rejected},
        "after_layer1_and_layer2": {**strip(both_scores), "rejected_findings": both_rejected},
        # Kept for the first consumer of this script.
        "scores": strip(raw_scores),
        "per_case": _per_case(cases, by_case),
    }


def _per_case(cases: list[dict], by_case: dict[str, list[Finding]]) -> list[dict]:
    from evals.metrics import strict_match

    rows = []
    for case in cases:
        findings = by_case.get(case["case_id"], [])
        rows.append({
            "case_id": case["case_id"],
            "is_real_defect": case["is_real_defect"],
            "defect_type": case["defect_type"],
            "findings": len(findings),
            "located": sum(1 for item in findings if item.start_line is not None),
            "strict_matches": sum(1 for item in findings if strict_match(item, case) is not None),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-score a recorded PRGuard run.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/prguard_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/evals/replay"))
    args = parser.parse_args(argv)

    document = json.loads(args.input.read_text(encoding="utf-8"))
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    result = replay(document, dataset)

    args.output.mkdir(parents=True, exist_ok=True)
    destination = args.output / f"replay-{args.input.parent.name}.json"
    destination.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"recorded prguard-review-v1 output re-scored with {EVALUATOR_VERSION}")
    for label, key in (("raw", "raw"), ("layer1", "after_layer1"), ("layer1+2", "after_layer1_and_layer2")):
        scores = result[key]
        rejected = scores.get("rejected_findings", 0)
        print(
            f"  {label:<9} unified P={scores['defect_precision']:.3f} R={scores['defect_recall']:.3f} "
            f"F1={scores['f1_score']:.3f} TP={scores['tp']} FP={scores['fp']} FN={scores['fn']} "
            f"adv={scores['advisory_count']} rejected={rejected}"
        )
    legacy = result["raw"]
    print(
        f"  legacy    P={legacy['legacy_precision']:.3f} R={legacy['legacy_recall']:.3f} "
        f"F1={legacy['legacy_f1']:.3f} TP={legacy['legacy_tp']} FP={legacy['legacy_fp']} FN={legacy['legacy_fn']}"
    )
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
