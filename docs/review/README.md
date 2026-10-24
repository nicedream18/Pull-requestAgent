# Review Guide

How to turn the 36 candidate cases from `draft` into a decision. This guide is written
for the human reviewer, not for the tooling.

## What exists

| File | Purpose |
| --- | --- |
| `candidates.md` | one section per case: before/after code, proposed labels, evidence line |
| `candidates.csv` | one row per labelled defect (plus one row per clean case), to fill in |
| `summary.md` | counts by status, the clean cases, and what is still pending |

Regenerate with:

```bash
PYTHONPATH=src .venv/bin/python -m evals.review_packet
PYTHONPATH=src .venv/bin/python -m evals.review_packet --split test
```

Regenerating overwrites the three files, so copy any filled-in CSV aside first if you
have edited it in place.

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
   `other_unlabelled_defects` rather than editing the JSON.
6. Only after all rows are filled should `prguard_v2.json` be updated, and then the
   `human_reviewed_count` must be set to the number of `reviewed` cases.

## Fields to fill in the CSV

| Column | What to put |
| --- | --- |
| `is_substantive_defect` | `yes`, `no`, or `indeterminate` |
| `other_unlabelled_defects` | free text, one per line, or `none` |
| `reviewer_notes` | why, especially for `needs_revision` and `indeterminate` |
| `review_status` | one of the four statuses above |

Leave a row blank rather than guessing. A blank row stays `draft`, which is the correct
state for "not reviewed yet".

## Suggested order

1. The 15 clean cases (`summary.md` lists them). These bound precision.
2. The 4 multi-defect cases. These bound recall and exercise dedup.
3. The 4 real-diff cases, including the rename.
4. The 3 `indeterminate` cases.
5. Everything else.

## When the set is ready

A run on this dataset may be described as a test-set measurement only when:

* every case is `reviewed` or `indeterminate`, and no case is `draft`;
* `human_reviewed_count` in the dataset matches the number of `reviewed` cases;
* `evals.check_labels --require-reviewed --strict` exits 0;
* the dataset hash and git commit are recorded with the result.

Until then, any number computed on this set is a pipeline measurement against
author-written drafts and must be labelled that way. See
[../evaluation-protocol.md](../evaluation-protocol.md) §2.
