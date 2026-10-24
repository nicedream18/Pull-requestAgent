# Metric Fix: Per-Category TP/FP/FN Accounting

**Date:** 2026-10-24
**Scope:** `evals/metrics.py`, both scorers
**Effect on published numbers:** global precision / recall / F1 unchanged; per-category
recall values were wrong before this fix and are corrected. No previously published
document quoted a per-category numeric value, so no historical claim has to be retracted.

## 1. The defect

Both `_legacy_score` and `_unified_score` accumulated per-category counts like this:

```python
if case_has_defect(case):
    for truth in truths:                                    # every labelled defect
        per_category[truth["defect_type"]]["fn"] += 1        # counted as a miss...
    for prediction_index in used_predictions:                # ...then also as a hit
        per_category[candidates[prediction_index].issue_type.value]["tp"] += 1
```

Two consequences:

1. **A matched defect was counted twice.** One correct prediction produced `tp=1` *and*
   `fn=1` for its category, so `tp + fn = 2` for a single labelled defect and per-category
   recall was halved for every category that worked.
2. **A misclassified prediction polluted the wrong bucket.** The `tp` was keyed by the
   *prediction's* `issue_type`, not the label's, so a wrong diagnosis credited a category
   that had no defect.

### Reproduction (before the fix)

One case, one `sql_injection` label, one correct prediction:

```
global tp/fp/fn: 1 0 0          <- correct
category_recall: {'sql_injection': 0.5}   <- should be 1.0
```

Per-category recall was therefore unusable for diagnosis, and `tp + fn` did not equal the
number of labelled defects in any category.

### Why the global numbers were unaffected

`true_positive`, `false_positive` and `false_negative` were accumulated separately from
the per-category dict, and those were computed correctly (`tp = len(used_predictions)`,
`fn = len(truths) - len(used_truths)`). Precision, recall, F1 and strict F1 were therefore
always right. Only the per-category breakdown was wrong.

## 2. The fix

A single shared accumulator now attributes each case's outcome once:

```python
for truth_index, truth in enumerate(truths):
    bucket = per_category[truth["defect_type"]]
    bucket["labelled"] += 1
    if truth_index in used_truths:
        bucket["tp"] += 1
    else:
        bucket["fn"] += 1
for prediction_index, finding in enumerate(candidates):
    if prediction_index not in used_predictions:
        per_category[finding.issue_type.value]["fp"] += 1
```

Rules now enforced:

| Rule | How |
| --- | --- |
| TP is a matched labelled defect, once | keyed by `truth["defect_type"]`, inside `used_truths` |
| FN is an unmatched labelled defect | keyed by `truth["defect_type"]` |
| FP is a prediction matching nothing | keyed by the prediction's own `issue_type` |
| `tp + fn == labelled` per category | only one of the two is incremented per truth |
| no double-counted TP | one increment per matched truth, not per prediction |
| one prediction matches at most one truth | `_greedy` marks both sides used |
| advisories stay separate | excluded from the defect view; added as FP only in `strict_*` |
| per-category P/R/F1 consistent with global | sums of tp/fn/fp equal the global counts |

New output keys: `category_stats` (defect view) and `strict_category_stats`, each with
`tp`, `fp`, `fn`, `labelled`, `precision`, `recall`, `f1`. `category_recall` is retained as
a convenience view of `category_stats[*].recall`.

## 3. Effect on the recorded runs

Global figures are byte-identical, which is the proof that only the breakdown changed:

| Run | Precision | Recall | F1 | TP | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v1 legacy replay, before | 0.093 | 0.700 | 0.165 | 7 | 68 | 3 |
| v1 legacy replay, after | 0.093 | 0.700 | 0.165 | 7 | 68 | 3 |
| Frozen ablation rung D, before | 0.800 | 0.800 | 0.800 | 8 | 2 | 2 |
| Frozen ablation rung D, after | 0.800 | 0.800 | 0.800 | 8 | 2 | 2 |

Per-category recall for the v1 legacy view, corrected:

| Category | before (wrong) | after |
| --- | ---: | ---: |
| `boundary_condition` | 0.50 | **1.00** |
| `division_by_zero` | 0.50 | **1.00** |
| `hardcoded_secret` | 0.50 | **1.00** |
| `null_dereference` | 0.50 | **1.00** |
| `resource_leak` | 0.50 | **1.00** |
| `sql_injection` | 0.50 | **1.00** |
| `unsafe_command` | 0.50 | **1.00** |
| `missing_exception_handling` | 0.00 | 0.00 |
| `missing_test_coverage` | 0.00 | 0.00 |
| `overly_permissive_file_mode` | 0.00 | 0.00 |

The *identification* of the three missed categories, as published in
`docs/benchmark.md`, was already correct: those were the three with recall 0 in both
scorers. Only the values for the found categories were half of what they should have been.

## 4. Tests added

`tests/test_category_metrics.py`, 10 tests, every expected count written out by hand
before the assertion. No test relies on `f1 == 1.0` alone.

| Test | Expected TP / FP / FN |
| --- | --- |
| two defects, different categories, both found | 2 / 0 / 0 |
| two defects, same category, one found | 1 / 0 / 1 |
| same defect predicted twice | 1 / 1 / 0 |
| defect reported under the wrong issue type | 0 / 1 / 1 |
| clean PR with one prediction | 0 / 1 / 0 |
| multi-defect PR plus an advisory | 2 / 0 / 0 (strict: 2 / 1 / 0) |
| mixed 4-case sweep | 3 / 1 / 3, and every `tp + fn == labelled` |
| one prediction, two nearby truths | 1 / 0 / 1 |
| advisory matching the label | 0 / 0 / 0 (strict: 0 / 1 / 0) |
| single-category precision matches global | 1 / 1 / 0 |

The sweep test also asserts that the per-category sums equal the global counts, so a
future change that reintroduces double counting fails immediately.

## 5. Was anything silently overwritten?

No. The old scorer is not preserved as a separate function, because the change is a bug
fix inside `_unified_score` and `_legacy_score`, not a new metric version. The global
values it produced are unchanged and are verified above. `EVALUATOR_VERSION` stays
`prguard-eval-v2` because the *definitions* of precision, recall and F1 are identical;
only a derived breakdown was corrected. If a downstream consumer depended on the old
(wrong) `category_recall`, it would now read 1.0 instead of 0.5 for a category that
worked — that is the fix, not a regression.

No published document quoted a per-category numeric value, so nothing needs retraction.
