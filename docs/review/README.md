# Review Guide

How to turn the 36 candidate cases from `draft` into a decision. This guide is written
for the human reviewer, not for the tooling.

## What exists

| File | Purpose |
| --- | --- |
| `candidates.md` | one section per case: before/after code, proposed labels, evidence line |
| `candidates.csv` | one row per labelled defect (plus one row per clean case), human input; do not regenerate in place |
| `summary.md` | counts by status, the clean cases, and what is still pending |
| `progress.md` | generated current progress report; it contains no inferred reviewer identity |
| `imports/` | append-only JSON snapshots created by explicit `--import` |

Generate a separate packet without touching this review sheet:

```bash
PYTHONPATH=src .venv/bin/python -m evals.review_packet --output /tmp/prguard-review-packet
PYTHONPATH=src .venv/bin/python -m evals.review_packet --split test --output /tmp/prguard-review-packet-test
```

The generator refuses to overwrite an existing `candidates.csv`. The source proposal file
`evals/datasets/prguard_v2.json` and all historical evaluation artifacts remain unchanged.

## What this packet is not

It is generated mechanically from the fixture code. It checks internal consistency only:

* the cited file exists and the line range is inside it;
* `supporting_evidence` really appears at the cited line;
* the deterministic condition check does not *contradict* the label.

`deterministic check | supported` means "the code contains the construct the label
describes". It does **not** mean the label is correct. `UNKNOWN` means the checker cannot
decide, which is the normal state for cross-file, test-gap, and interprocedural claims.

No model produced these labels, and no model may sign them off.

## Statuses

| Status | Meaning |
| --- | --- |
| `draft` | not yet looked at by a human |
| `reviewed` | a named human confirmed the defect, its line range and its severity |
| `needs_revision` | the defect exists but the label (line, severity, or type) is wrong |
| `indeterminate` | the code alone cannot decide; record why in `reviewer_notes` |

Three cases start as `indeterminate` because the author already knows the code cannot
settle them: `safe-subprocess-16`, `tricky-multiline-sql-28`, `real-diff-guard-line-36`.
Confirm or override each.

## Per-case procedure

1. Read the before/after code. Ignore the proposed labels on the first pass.
2. Ask: **is there a defect here at all, and where exactly?** Write down file and line
   before looking at the proposal.
3. Compare with the proposal. Record one of the four statuses.
4. For a clean case, spend extra effort: a missed defect there silently inflates measured
   precision. The 15 clean cases are listed in `summary.md`.
5. If the proposal has a defect the fixture does not label, put it in
   `other_unlabelled_defects`; use `needs_revision` until a corrected dataset version exists.
6. Never edit an old dataset or evaluation artifact. Corrections belong in a new dataset
   version with a corresponding new review packet.

## Fields to fill in the CSV

| Column | What to put |
| --- | --- |
| `is_substantive_defect` | `yes`, `no`, or `indeterminate` |
| `other_unlabelled_defects` | free text, one per line, or `none` |
| `reviewer_notes` | why, especially for `needs_revision` and `indeterminate` |
| `review_status` | one of the four statuses above |
| `reviewer_id` | the actual human reviewer; never use an agent or tool name |
| `reviewed_at` | review time as ISO-8601 with timezone, for example `2026-10-24T14:30:00+08:00` |
| `review_version` | the human review protocol/version used for this decision |

Leave a row blank rather than guessing. A blank row stays `draft`, which is the correct
state for "not reviewed yet".

For a reviewed defect row, set `is_substantive_defect=yes`; for a reviewed no-defect row,
set it to `no`. If the candidate conclusion, location, severity, or issue type needs
correction, use `needs_revision` and explain it in `reviewer_notes`. An audited
`indeterminate` decision requires the opinion `indeterminate`, notes, and all three audit
fields. The three initial `indeterminate` statuses already in this sheet have no reviewer
metadata and count as unresolved proposals, not completed human reviews.

Validate and publish a progress report:

```bash
PYTHONPATH=src .venv/bin/python -m evals.import_reviews --report docs/review/progress.md
```

After every case is `reviewed`, create a new human-reviewed dataset version without
changing the candidate JSON or overwriting an existing file:

```bash
PYTHONPATH=src .venv/bin/python -m evals.import_reviews \
  --require-reviewed \
  --build-reviewed-dataset evals/datasets/prguard_v2_reviewed.json
```

The new file carries the source CSV hash and per-case reviewer decisions. Validate that
specific file before freezing or scoring it:

```bash
PYTHONPATH=src .venv/bin/python -m evals.check_labels \
  --dataset evals/datasets/prguard_v2_reviewed.json --strict --require-reviewed
```

A `needs_revision` decision requires a corrected, newly versioned candidate dataset and
a new review packet; the importer never edits defect labels in place.

Archive the current CSV and decisions without overwriting any prior snapshot:

```bash
PYTHONPATH=src .venv/bin/python -m evals.import_reviews --import
```

The import checks case/defect identifiers, row coverage, duplicate/conflicting decisions,
clean-case review, status/opinion consistency, reviewer metadata, and timestamps. Partial
snapshots can preserve work in progress, but they are not eligible for official metrics.
The formal gate is:

```bash
PYTHONPATH=src .venv/bin/python -m evals.import_reviews --require-reviewed
```

This gate must pass on a human-reviewed, corrected dataset version before any test or
holdout run. `needs_revision`, `indeterminate`, and `draft` cases remain outside official
metrics; the importer does not modify the dataset or make corrections on the reviewer's
behalf.

## Suggested order

1. The 15 clean cases (`summary.md` lists them). These bound precision.
2. The 4 multi-defect cases. These bound recall and exercise dedup.
3. The 4 real-diff cases, including the rename.
4. The 3 `indeterminate` cases.
5. Everything else.

## When the set is ready

A run on this dataset may be described as a test-set measurement only when:

* every case, including all 15 no-defect cases, is fully `reviewed`;
* all `needs_revision` decisions have been resolved into a new versioned dataset;
* the human-reviewed count and review snapshot agree with the dataset;
* strict label validation passes;
* dataset hash, audit snapshot hash, git commit, prompt SHAs and evaluator version are frozen together.

Until then, no metrics on this set may be presented as formal benchmark results. The 15
development fixtures remain separate pipeline regressions and must never be mixed into the
independent candidate test/holdout score. See
[../evaluation-protocol.md](../evaluation-protocol.md) §2.
