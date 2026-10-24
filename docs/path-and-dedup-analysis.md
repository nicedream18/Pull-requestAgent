# Path and Dedup Analysis

Audit of the 28 findings produced by the first valid online run
(`artifacts/evals/ablation/20261024T102247Z/ablation.json`, prompt `prguard-review-v2`,
`deepseek/deepseek-chat`). The original artifact is read-only and was not modified.

Every number here is derived from that file. Nothing was re-generated.

## 1. Why 21 findings were rejected

All 21 rejections were `invalid_path`. They are not one problem but four:

| Category | Count | Example | Recoverable? |
| --- | ---: | --- | --- |
| Placeholder | 13 | `unknown`, `unknown (implementation not shown)` | yes, if evidence is unique |
| Empty string | 3 | `""` | yes, if evidence is unique |
| Bare basename | 4 | `export.py` for `app/export.py` | yes, if the basename is unique in the diff |
| Wrong specific file | 1 | `app/users.py`, which is not in the diff | only from evidence, and it must be labelled |

The 7 valid paths were: `app/search.py`, `app/uploads.py` x2, `app/settings.py` x3,
`app/runner.py`.

Only 25% of findings named a usable file. That, not the reasoning, is the dominant
defect of the run.

## 2. Field completeness of the 28 findings

| Field | Present and usable | Notes |
| --- | ---: | --- |
| `issue_type` (non-`other`) | 28 | closed vocabulary worked |
| `start_line` | 28 | every finding cited a line |
| `evidence` | 28 | all non-empty |
| `evidence` present verbatim in the source | 28 | no fabricated quotes |
| `start_line` agrees with where the evidence sits (+/-2) | 27 | `test-gap-07` cites line 1 while its evidence sits at line 4 |
| `file_path` valid | 7 | the failure above |
| `trigger_condition` | 28 | all stated, wording varies |

So the model's *content* is sound; its *location* is not. That distinction drives the
whole change: recover the location from data the pipeline already trusts, and never
weaken the location check.

## 3. Which findings can be safely recovered

The rule implemented in `prguard/paths.py` accepts only these bases:

1. the path equals a changed file (`exact`);
2. the path normalizes to a changed file (`normalized`): separators, `./`, `a/`/`b/`
   prefixes, duplicate slashes, surrounding whitespace, trailing slash;
3. a bare basename matches exactly one changed file (`normalized`);
4. otherwise, the evidence appears verbatim in exactly one changed file **and** the
   cited line is within two lines of where that evidence sits
   (`evidence_resolved`).

Anything else is `ambiguous` or `unresolved` and the finding is rejected, with the
original value preserved in `reported_path`.

Applied to the 28 findings:

| Status | Count |
| --- | ---: |
| `exact` | 7 |
| `normalized` | 2 |
| `evidence_resolved` | 18 |
| `unresolved` | 1 |
| `ambiguous` | 0 |

The one unresolved case is `test-gap-07`: its evidence (`source.debit(amount)`) sits at
line 4 but the finding cites line 1, a gap of 3 which exceeds the tolerance. It is
rejected rather than forced onto the only file in the diff.

Two recoveries deserve scrutiny:

* `sql-injection-05` reported `app/users.py`, a file that does not exist. Its evidence
  (`cursor.execute(f"SELECT * FROM users WHERE name = '{name}'")`) appears only in
  `app/search.py`, at the cited line, so it is recovered. The status is
  `evidence_resolved` and the basis states the original path was not a changed file, so
  the report shows the correction rather than hiding it.
* `safe-subprocess-13` reported `unknown` three times. Recovery points all three at
  `app/runner.py`, which is correct as a location but the *claims* are about safe code.
  Recovery is not a correctness guarantee; that is what layer 2 is for (§6).

## 4. Which findings must stay rejected

| Case | Reason |
| --- | --- |
| `test-gap-07` | line and evidence disagree; cannot pin a location |
| anything with evidence shorter than 8 characters | cannot identify a file |
| any evidence found in two changed files | `ambiguous`, never "first match wins" |
| any path that is a placeholder and has no matching evidence | `unresolved` |

The resolver never consults ground truth and never falls back to "the diff has one file,
so use it". That last shortcut is explicitly excluded by a test
(`test_unknown_path_is_not_resolved_just_because_one_file_exists`).

## 5. Duplicate findings

The two remaining false positives in the first run were duplicates, confirmed against
the raw artifact:

| Case | Findings | Same evidence? | Same line? | Same issue_type? |
| --- | ---: | --- | --- | --- |
| `permissions-11` | 2 valid-path | yes, `os.chmod(path, 0o777)` | yes, line 4 | yes, `overly_permissive_file_mode` |
| `secret-14` | 3 valid-path | yes, `API_TOKEN = "sk-live-..."` | yes, line 1 | yes, `hardcoded_secret` |

The old `_same_issue` compared normalized *text*. Chinese has no word boundaries, so the
Jaccard overlap between two paraphrases of the same defect was 0.14-0.22 against a 0.42
threshold, and nothing merged.

The new rule ranks signals by how much they prove:

1. same file after normalization;
2. lines within two (or both absent);
3. two different specific `issue_type` values are different defects;
4. **byte-identical evidence is the same citation** — this is the decisive signal, and it
   holds even when the two agents describe the trigger differently, which they do;
5. when the evidence differs, a stated pair of disjoint trigger conditions means two
   different defects, so no merge; when the evidence agrees on a line but the text
   differs, require 0.6 evidence overlap;
6. only then fall back to wording similarity (0.42, or 0.25 on the same line).

Rule 4 is what makes `permissions-11` and `secret-14` merge. Rule 3 and rule 5 are what
stop two genuine defects in one function from collapsing into one.

### What must not merge, and is tested

| Situation | Result |
| --- | --- |
| same function, `resource_leak` + `missing_exception_handling` | not merged |
| same line, `sql_injection` + `hardcoded_secret` | not merged |
| similar Chinese description, different evidence and disjoint triggers | not merged |
| lines 2 and 40 in the same file | not merged |
| `app/service.py` vs `./app/service.py` | merged |

## 6. Effect on the metrics

See `docs/ablation-results.md` §16 for the full A/B/C/D table. Summary on the frozen
findings:

| Config | P | R | F1 | TP | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A layer 1 only | 0.667 | 0.400 | 0.500 | 4 | 2 | 6 |
| B + path recovery | 0.333 | 0.800 | 0.471 | 8 | 16 | 2 |
| C + dedup | 0.615 | 0.800 | 0.696 | 8 | 5 | 2 |
| D + layer 2 | 0.800 | 0.800 | 0.800 | 8 | 2 | 2 |

Path recovery doubles recall and **halves precision**; only after dedup and layer 2 does
the combination beat the baseline on all three metrics. That is the honest shape of the
result and it is why B alone must not be presented as an improvement.

## 7. Residual issues

1. **Single-file fixtures cannot test mis-recovery.** With one changed file, evidence
   recovery can only point at that file, so "path mis-recovery count" is structurally
   zero here. Multi-file safety is covered by unit tests only, not by real data. This is
   the most important gap and is addressed by the candidate dataset in
   `docs/dataset-design.md`.
2. **Two false positives survive in D, and both are `missing_exception_handling`.** They
   are not verifier failures: on `shell-injection-06` the code really does call
   `subprocess.run(user_arg, shell=True)` without `check=True`, and on
   `safe-subprocess-13` it really does use `check=True` with no `try`. Layer 2 correctly
   returns `SUPPORTED` for both. The fixtures simply label one defect per PR, so a
   second real observation is charged as a false positive. This is label incompleteness,
   not a pipeline defect.
3. **`test-gap-07` is a genuine miss** for the second run in a row. The model produced no
   findable location, and a diff cannot prove that no test exists.
