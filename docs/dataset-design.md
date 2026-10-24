# Candidate Evaluation Set Design

`prguard_v1.json` (15 single-file fixtures) has been used for every round of tuning in
this branch. It can no longer serve as evidence of generalisation: rules were adjusted
against it repeatedly, and each fixture carries exactly one label, so a second correct
observation is always charged as a false positive.

This document specifies a **candidate** set. It contains no labels yet. Labels must be
written by a human reviewer and frozen before any run, and the set must not be used to
tune the pipeline afterwards.

## 1. Status

| Item | Status |
| --- | --- |
| Schema and case inventory | specified below |
| Files and code | not written |
| Labels | **not written**; no LLM may produce them |
| Frozen | no |
| Used for tuning | must remain no |

Nothing in this document has been measured. It is a plan only.

## 2. Required coverage

Each row becomes at least one case. Cases marked *multi* need more than one changed file.

| # | Property under test | Shape | Why it matters |
| --- | --- | --- | --- |
| 1 | multi-file PR, one defect | 3-5 changed files, defect in one | path recovery must pick the right file |
| 2 | same basename in two directories | `app/settings.py` + `tests/settings.py`, defect in one | must be `ambiguous`, never "first match" |
| 3 | one PR, several defects | 2-3 defects across files | recall must not assume one defect per PR |
| 4 | no defect at all | clean refactor | false-positive pressure |
| 5 | cross-file breakage | caller/callee in different files | `cross_file_breakage` path |
| 6 | two independent defects in one function | e.g. leak + missing handler | dedup must not merge them |
| 7 | incomplete or wrong evidence | evidence paraphrased, not verbatim | layer 1 must reject |
| 8 | correct claim, missing path | evidence unique to one file | recovery must succeed |
| 9 | wrong path that looks plausible | `app/users.py` in a repo with `app/search.py` | recovery only via evidence, and labelled |
| 10 | safe counterpart of a real pattern | parameterized query, `shell=False`, `with open` | layer 2 must refute |
| 11 | documentation-only change | `.md` only | routing must stay low-risk |
| 12 | test present but covering only the happy path | test file in context | three-level coverage rule |
| 13 | guard present but not dominating | bounds check after the read | layer 2 must not clear it |
| 14 | evidence appears in two changed files | identical helper in two modules | must be `ambiguous` |

## 3. Case schema

One case per PR, with a list of defects rather than a single label:

```json
{
  "case_id": "multi-01",
  "pr_title": "refactor: split settings loader",
  "files": [
    {"path": "app/settings.py", "code": "..."},
    {"path": "tests/test_settings.py", "code": "..."}
  ],
  "diff": "@@ ... unified diff ...",
  "defects": [
    {
      "defect_id": "multi-01-d1",
      "issue_type": "hardcoded_secret",
      "file_path": "app/settings.py",
      "line_range": [3, 3],
      "severity": "critical",
      "supporting_evidence": "API_TOKEN = \"sk-live-...\"",
      "is_real_defect": true
    }
  ],
  "has_defect": true,
  "label_status": "human_reviewed",
  "label_notes": "free text from the reviewer, including anything ambiguous"
}
```

Field notes:

* `defects: []` with `has_defect: false` encodes a clean PR explicitly.
* `label_status` is one of `draft`, `human_reviewed`, `disputed`. Only
  `human_reviewed` cases may be scored.
* `supporting_evidence` must be copied verbatim from the file; the evaluator uses it to
  check that the label itself is consistent with the code.
* A defect that a reviewer considers arguable goes in `label_notes`, not in `defects`,
  so the score stays defensible.

## 4. Split

| Split | Target | Rule |
| --- | --- | --- |
| dev | 20 cases | may be inspected while changing rules |
| test | 20 cases | **never** inspect per-case output while tuning; run once per candidate change |
| holdout | 10 cases | run once, at the end, when the pipeline is frozen |

The three splits must not share a case. A case that was ever inspected during tuning
belongs to dev, permanently.

## 5. Label procedure

1. Write the code and a description of the intended defect, if any.
2. A different person reads the code without seeing the description and records every
   defect they find, with line and evidence.
3. Reconcile the two: disagreements go to `label_notes`, and the case is marked
   `disputed` until resolved.
4. Any case whose label would change after seeing model output is deleted, not patched.

An LLM may be used to *find candidate* defects for the reviewer to check, but its output
may never be committed as a label, and `label_status` may not become `human_reviewed`
without the step above.

## 6. Anti-overfitting rules

* Tune only on `dev`.
* Report `test` results once per change; if a rule was changed *because* of a test case,
  move that case to `dev` and drop it from `test`.
* Never edit a label to make a run look better. A disputed label is `disputed`, not
  corrected toward the model.
* Publish the split names and case counts with any result, so a reader can see how much
  data was available.

## 7. What each planned metric needs

| Metric | Requires |
| --- | --- |
| mis-recovery rate | a multi-file case where the true file differs from the recovered one (#1, #9, #14) |
| dedup precision | a case with two real defects in one function (#6) |
| dedup recall | a case where two agents paraphrase one defect (#2, #8) |
| layer-2 value | a case with a safe counterpart (#10) plus a flawed guard (#13) |
| recall ceiling | multi-defect cases (#3) so one miss is not 1/1 of the case |

The current set supports none of these, which is the strongest argument for building this
one before claiming generalisation.
