# False Positive Analysis

Audit of the first online evaluation of PRGuard, run on 2026-10-24 with `deepseek/deepseek-chat`
and prompt `prguard-review-v1` over the 15 fixtures in `evals/datasets/prguard_v1.json`.

Raw data: `artifacts/evals/online/20261024T081509Z/` (the `artifacts/` directory is git-ignored, so
the raw JSON stays on the machine that produced it). Re-run it with:

```bash
PYTHONPATH=src .venv/bin/python -m evals.run --mode online --strategy dynamic \
  --model deepseek/deepseek-chat --output artifacts/evals/online
```

Every number below is derived from that file plus a per-finding manual classification. No finding was
re-generated, and the ground truth was not edited.

## 1. Headline result

| Metric | Value |
| --- | ---: |
| Confirmed defects under the old matcher (TP) | 7 |
| False positives (FP) | 68 |
| False negatives (FN) | 3 |
| Precision / Recall / F1 | 0.093 / 0.700 / 0.165 |
| Raw findings produced by agents | 94 |
| Findings accepted by the old verifier | 75 |
| Findings rejected by the old verifier | 19 |

The 19 rejections were all fabricated locations or evidence: 14 `invalid_path`, 3 `invalid_line`,
2 `evidence_mismatch`. Layer 1 worked; nothing invalid got through.

## 2. Measurement defects found first

Two properties of the old matcher make a large share of the 68 labelled false positives impossible to
score as true positives, regardless of whether the model was right:

| Property | Count | Share of 75 |
| --- | ---: | ---: |
| Findings with no `start_line` | 34 | 45.3% |
| Findings written in Chinese | 66 | 88.0% |
| Chinese findings with no `start_line` | 34 | 45.3% |

* `_match_score` returned `None` whenever `start_line` was missing, so 45.3% of findings had a zero
  chance of being credited.
* All 15 ground-truth descriptions are English. The prompt requires Chinese titles and descriptions, so
  the token-overlap gate (`>= 0.045`) compared disjoint alphabets. For `permissions-11` every one of the
  seven findings scored Jaccard `0.000` against the label, including the finding that named `0o777`
  exactly.

Only 9 findings in the whole run were English, and only 4 of those were English *and* carried a line
number inside a defect case. That is the entire pool the old metric could ever score.

This is a measurement artifact, not a model improvement opportunity, and it is fixed in
`evals/metrics.py` by matching on the new `issue_type` field (a closed vocabulary shared with the
labels) with the line window and file equality still enforced.

## 3. Manual classification of the 68 false positives

Each of the 68 was read against its fixture source and assigned to exactly one bucket.

| Bucket | Count | Share | Is the claim wrong? |
| --- | ---: | ---: | --- |
| A. Same labeled defect, lost to the language gate (has correct line) | 1 | 1.5% | No, correct |
| B. Same labeled defect, lost because no line number was given | 8 | 11.8% | No, correct |
| C. Duplicate of another finding in the same case | 5 | 7.4% | No, redundant |
| D. Test-gap claim without a provable behavior risk | 29 | 42.6% | Not a defect |
| E. Style / preference (missing docstring, `SELECT *`, type hints) | 10 | 14.7% | Not a defect |
| F. Plausible observation the fixture does not label (encoding, timeout, path validation) | 15 | 22.1% | Real, but unlabeled |

Reading the table:

* **44 of 68 (64.7%) are not defects** under the classification this change introduces: D and E are
  advice, and C is redundancy. These are the findings a reviewer would rightly call noise.
* **15 of 68 (22.1%) are real observations that the fixture does not label.** This is label
  incompleteness, and it is why one labelled defect per fixture cannot support a precision claim.
* **9 of 68 (13.2%) are the correct answer, lost to matching mechanics.** A, B. The model found these
  defects and the metric threw them away.

Bucket B is worth separating from A: the model named the right defect but omitted `start_line`.
`boundary-03` ("`last_item` 的循环多迭代一次"), `shell-injection-06` ("Shell command injection via
shell=True"), `permissions-11` (two `0o777` findings), `secret-14` (three hardcoded-token findings) and
`test-gap-07` all fall here.

## 4. Answers to the seven review questions

### 4.1 Which findings are real false positives?

Buckets C, D and E: 44 findings. Of those, D and E (39 findings) are claims that should never have been
emitted as defects, and C (5 findings) are the same issue repeated by a second agent.

### 4.2 Which are false positives caused by incomplete labels?

Bucket F: 15 findings (22.1%). Typical cases: `resource-leak-04` "未指定文件编码", `shell-injection-06`
"未设置超时导致子进程可能无限挂起", `safe-subprocess-13` "No timeout on subprocess call". All are
defensible engineering observations on the changed lines; the fixture labels only one defect per PR, so
each is charged as a false positive.

### 4.3 Which problems come from agent responsibility overlap?

5 findings survived deduplication even though another agent reported the same problem:
`secret-14` was reported by four agents (CodeQuality, Security, Context, TestImpact) and
`permissions-11` by three. The old `_same_issue` compared normalised text, and because agents answered
in different languages the same defect never merged. Across the whole run there are 40 redundant
same-case pairs (`file_path` + `category` + line within 2), so deduplication under-merges badly.

### 4.4 Why does TestImpactAgent produce so many findings?

39 findings, 52% of everything produced, but only 1 true positive (precision 0.026).

| Category | Findings |
| --- | ---: |
| testing | 30 |
| bug | 6 |
| quality | 2 |
| security | 1 |

The v1 prompt said "identify behavior changes that lack tests and missing boundary or failure-path
coverage". For a diff that ships no tests, "this change lacks tests" is always true, so the agent was
rewarded for restating an invariant. The prompt contained no requirement that the change carry behavior
risk, no instruction to prefer an empty list, and no separation between a defect and a coverage
preference. It therefore emitted a finding for essentially every function it saw, including pure helpers
like `clamp` and `transfer_funds`.

### 4.5 Why are path, line and evidence checks insufficient?

Layer 1 answers "does this text exist where you say it does", not "is the claim true". A finding on
`safe-sql-08` that quotes the real parameterized line passes all three checks while being completely
wrong; only a check of the defect condition refutes it. Concretely, 14 of the 19 rejections were
fabricated paths, which shows layer 1 does stop invented locations, but nothing in layer 1 examines
whether untrusted input actually reaches a sink, whether a handle is really unclosed, or whether a
test gap implies risk.

### 4.6 Does the matching rule wrongly penalise semantically equivalent results?

Yes, in four ways, all reproduced above:

1. Language: Chinese findings against English labels score 0 overlap.
2. Missing line: a finding without `start_line` can never match.
3. Category equality: `exception-02` reported the right defect three times on the right line, but as
   `testing` instead of `bug`, so it scored zero.
4. One label per fixture: correct extra observations are charged as false positives (bucket F).

### 4.7 Why were some labelled defects not found?

| Missed defect | What happened |
| --- | --- |
| `exception-02` missing_exception_handling | Reported three times on line 4, all as `testing`. The issue was noticed; the classification was wrong. |
| `test-gap-07` missing_test_coverage | Reported three times, all without a line number, so unscoreable. |
| `permissions-11` overly_permissive_file_mode | Reported at the exact line by CodeQuality, TestImpact and Security; every one lost to the language gate. |

Strictly speaking the model missed one defect (`missing_exception_handling` misclassified), and the
metric missed two.

## 5. What this changes in the product

The audit drove three code changes, all in this branch:

| Change | File | Effect on this audit's causes |
| --- | --- | --- |
| `issue_type` closed vocabulary, matched structurally in the evaluator | `models.py`, `metrics.py` | Removes cause 4.6.1 and 4.6.2 from the measurement; a correct issue on the right line now scores regardless of output language or missing line |
| `finding_type` defect/advisory plus a required `trigger_condition` for defects | `models.py` | Moves bucket D and E out of the defect count instead of silently scoring them as wrong |
| AST condition checks in the verifier | `evidence.py`, `verifier.py` | Rejects 4.5 style false claims (`condition_not_met`) and demotes the ones it cannot decide to advisory |

The evaluator now reports `defect_precision` (advisories excluded) **and** `strict_precision`
(advisories counted as false positives) plus `advisory_rate`, so relabelling everything as advice
cannot raise the headline number.

## 6. Limits of this analysis

* The classification in section 3 is a manual read of 68 findings by one reviewer. A second reviewer
  could reasonably move a finding between D, E and F; buckets A, B and C are mechanical and stable.
* The fixtures are 9-line snippets. Nothing here measures behaviour on a real multi-hundred-line PR, and
  the low token counts (3079 per PR) reflect that.
* No conclusion about whether the *new* pipeline scores better is drawn here. That requires a fresh
  online run with prompt `prguard-review-v2`, which has not been executed.
