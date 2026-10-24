# Ablation Results

Status: **stages 1 and 2 complete, stage 3 prepared but not executed against a paid
model.** Every number in this file comes from a recorded file or a command that was
actually run in this workspace. Nothing is projected.

- Branch: `feat/false-positive-reduction`
- Evaluator: `prguard-eval-v2`
- **Rungs B and C are still unmeasured.** `evals.ablation` is the harness that will
  produce them from a single model pass; it has only been run with the deterministic
  offline runner.
- Dataset: `evals/datasets/prguard_v1.json` v1.0, 15 fixtures (10 labelled defects, 5 clean)
- Recorded baseline: `artifacts/evals/online/20261024T081509Z/results.json`
  (prompt `prguard-review-v1`, `deepseek/deepseek-chat`, run on 2026-10-24)

## 1. What was fixed in this stage

| Area | Before | After |
| --- | --- | --- |
| Test-coverage check | any `test_*.py` in context refuted every module's test-gap claim; a module test file refuted it as soon as the function name appeared in it | three levels: a test asserting an exception around the function refutes the gap; a happy-path call or a bare mention returns `UNKNOWN` |
| AST scope | checks walked the whole file, so a defect at line 2 proved a finding at line 5 in a different function | checks walk only the resolved block: the enclosing function, or module-level statements |
| AST call site | inside one function, a check judged the *first* matching call, so a safe call at line 3 refuted a finding on the risky call at line 5 | candidates are tied to the reported line; if none is, the answer is `UNKNOWN` instead of borrowing another call site |
| Bounds guard | any length comparison anywhere in the loop counted as a guard, including one placed after the read | the guard must dominate every read: reject-style guards must precede them, positive guards must contain them |
| Boundary check | `range(len(x) + 1)` anywhere in the file was `SUPPORTED` | requires the loop index to actually be used to subscript, and no in-loop bounds guard |
| Verifier mutation | `verify_findings` wrote `evidence_level`, `verification_status` and `trigger_condition` onto the caller's objects | it verifies a deep copy; the flow also freezes `state.raw_findings` before verification |
| Matching | file + category + line window + token overlap; a finding without `start_line` could match | defect only, same file, concrete line within ±2, evidence present verbatim in the changed file, then `issue_type` equality or a documented text fallback |
| `strict_precision` | advices were concatenated with defects, so an advisory with the right `issue_type` could be a TP | only a `defect` can be a TP; every advisory is a FP in `strict_*` |
| Legacy metrics | the published scorer was edited in place | frozen as `_legacy_score`; four separate metric views are reported |
| Condition checks | missing construct often returned `CONTRADICTED` | missing construct returns `UNKNOWN`; only positive evidence returns `SUPPORTED` |
| Demotion | Pydantic rewrote `finding_type` on construction, corrupting recorded data | the model only sets a flag; the verifier demotes |
| Run artifacts | no evaluator or commit recorded | `evaluator_version`, `git_commit`, `condition_checks`, `strict_*`, `legacy_*` in `results.json`, `results.csv` and `benchmark.md` |

### Metric views now reported

| Key | Meaning | Used for |
| --- | --- | --- |
| `defect_precision` / `defect_recall` / `f1_score` | unified matcher, advisories excluded from the denominator | confirmed-defect quality |
| `strict_precision` / `strict_recall` / `strict_f1` | unified matcher, advisories counted as false positives | cannot be gamed by relabelling |
| `pre_verifier_*` / `post_verifier_*` | same matcher, before and after the verifier | isolating what the verifier changed |
| `legacy_*` | frozen v1 matcher | reproducing history only |

`advisory_rate`, `mean_findings_per_pr`, `false_positives_per_no_defect_pr`,
`verifier_rejection_rate`, `rejection_types`, `agent_stats` and `match_channels`
are reported alongside, so a precision gain bought by rejecting output shows up as
a recall loss in the same table.

## 2. Re-scoring the recorded v1 run

Command actually run:

```bash
PYTHONPATH=src .venv/bin/python -m evals.replay_v1 \
  --input artifacts/evals/online/20261024T081509Z/results.json \
  --output artifacts/evals/replay
```

| View | Precision | Recall | F1 | TP | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Legacy (published) | 0.093 | 0.700 | 0.165 | 7 | 68 | 3 |
| Legacy recomputed by the frozen function | 0.093 | 0.700 | 0.165 | 7 | 68 | 3 |
| Unified, same raw output | **0.013** | **0.100** | **0.024** | 1 | 74 | 9 |
| Unified, after layer 1 replay | 0.013 | 0.100 | 0.024 | 1 | 74 | 9 |
| Unified, after layer 1 + 2 replay | 0.013 | 0.100 | 0.024 | 1 | 74 | 9 |

The legacy row reproduces the published numbers **exactly**, which is the point of
freezing it. The unified row is not a quality judgement about the model; it is a
statement about what the recorded output can prove.

### Why the two differ by that much

Funnel over the 75 recorded findings:

| Gate | Findings passing |
| --- | ---: |
| total recorded | 75 |
| same file as the label | 75 |
| carries a `start_line` | 41 |
| evidence appears verbatim in the source | 75 |
| `category` equals the label `category` | 21 |
| `issue_type` equals the label `defect_type` | **0** |
| final unified match (`structured` / `text`) | 1 (0 / 1) |

Two causes, both measured:

1. **v1 had no classification field.** Every finding maps to `issue_type=other`, so
   the structured channel is unavailable and only the text channel can fire. This is
   a limitation of the v1 output schema, not of the model's judgement.
2. **The text channel compares Chinese output with English labels.** 21 findings
   reach the category gate and almost none clear the 0.20 token-overlap gate.

The replay also cannot re-derive the 19 findings the original verifier rejected,
because the recorded file only stores post-verifier output. A replayed rejection
count of 0 is therefore expected, not evidence that layer 1 is inert.

**This difference must not be reported as an improvement.** The v1 pipeline is
penalised by the unified matcher largely because it cannot express what it found.

## 3. Rungs

| Rung | Configuration | Status | Precision | Recall | F1 |
| --- | --- | --- | ---: | ---: | ---: |
| A0 | v1 output, legacy matcher | measured (recorded file) | 0.093 | 0.700 | 0.165 |
| A1 | v1 output, unified matcher | measured (replay) | 0.013 | 0.100 | 0.024 |
| A2 | v1 output through the v2 verifier, unified matcher | measured (replay) | 0.013 | 0.100 | 0.024 |
| B | v2 prompt, layer 1 only (`--condition-checks off`) | **not run**; harness ready (`evals.ablation`) | — | — | — |
| C | v2 prompt, layer 1 + 2 (`--condition-checks on`) | **not run**; harness ready (`evals.ablation`) | — | — | — |
| D | C + conditional critic | not implemented | — | — | — |

A2 is identical to A1 because `require_trigger=False` is passed during replay: prompt
v1 cannot emit a trigger condition, and charging the verifier for a prompt limitation
would mix two variables. With that disabled, layer 1 has nothing to reject in an
already-verified recording, and layer 2 cannot fire because `issue_type` is `other`.

**Consequence for the experiment design:** B and C are the only rungs that can
isolate a real effect, because they share the same prompt and differ only in the
`condition_checks` flag. B/A comparisons mix a prompt change with a measurement
change and must be labeled as such.

## 4. The single offline false negative

`dynamic` recall is 0.900 rather than 1.000, and exactly one labelled defect is
unconfirmed: `test-gap-07` (`missing_test_coverage`).

The finding is still produced and still reported. The verifier demotes it to
`advisory` with:

```
the changed code is risky but no test content was retrieved, and a bounded
context cannot prove absence
```

Why this is correct rather than a bug: the fixture is a single 9-line file with no test
directory, and the claim is "the transfer has no test for its debit/credit failure
path". Nothing in the supplied context can prove a test does not exist — only a whole
repository listing could. Returning `SUPPORTED` would mean asserting an absence from a
bounded window, which is exactly the kind of unprovable claim this work removes.

Recall is therefore not recovered by weakening the rule. It can only move if the
retrieval step supplies the test files, at which point the three-level check decides:
a `pytest.raises` around the function refutes the gap, a happy-path call or a bare
mention does not. The expected effect of that retrieval work is one labelled defect
moving from advisory back to confirmed, not a change to the metric.

## 5. Offline baseline after all changes

Command:

```bash
PYTHONPATH=src .venv/bin/python -m evals.run --mode offline --output artifacts/evals
```

| Strategy | Precision | Recall | F1 | Strict F1 | Pre-verifier R | Post-verifier R | TP | FP | FN | Advisory rate | Findings/PR | FP per clean PR | Input tok/PR | Calls/PR | Mean s | P95 s | Est. USD | Agent fail rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| single | 1.000 | 0.500 | 0.667 | 0.667 | 0.500 | 0.500 | 5 | 0 | 5 | 0.000 | 0.33 | 0.00 | 42.6 | 1.00 | 0.000 | 0.001 | n/a | 0.000 |
| static | 1.000 | 0.900 | 0.947 | 0.900 | 1.000 | 0.900 | 9 | 0 | 1 | 0.100 | 0.67 | 0.00 | 170.4 | 4.00 | 0.000 | 0.001 | n/a | 0.000 |
| dynamic | 1.000 | 0.900 | 0.947 | 0.900 | 1.000 | 0.900 | 9 | 0 | 1 | 0.100 | 0.67 | 0.00 | 117.1 | 2.73 | 0.000 | 0.001 | n/a | 0.000 |


The pre-verifier and post-verifier columns are the direct measurement of what the verifier
does to the offline fixtures: recall falls from 1.000 to 0.900 and precision stays at
1.000, so on this dataset the verifier costs one detection and buys no false-positive
reduction. That is an honest negative result on the fixtures and the reason B/C exist.

Dynamic recall fell from 1.000 to 0.900 in this stage. The cause is deliberate:
`test-gap-07` is labelled `missing_test_coverage`, and the verifier now returns
`UNKNOWN` for it because a diff cannot prove that no test exists. The finding is
still reported, as an advisory flagged for human review, and `advisory_rate` shows
the trade explicitly. The CI assertion was updated to permit exactly this one miss
and to fail on any other change.

## 6. Verifier contribution, measured without an LLM

The layer-1/layer-2 split is covered by tests rather than by the replay, because the
recording has already been through a verifier. `tests/test_false_positive_controls.py`
drives the full flow with a false SQL-injection claim against the parameterized
fixture:

| Rung | Verified findings | `condition_not_met` errors |
| --- | ---: | ---: |
| `condition_checks=False` | 1 | 0 |
| `condition_checks=True` | 0 | 1 |

That is a real, reproducible measurement of layer 2, but it is one synthetic claim.
It shows the mechanism works; it does not quantify the FP reduction on live output.

## 7. Cost

Measured from the recorded v1 run:

| Quantity | Value |
| --- | ---: |
| LLM calls per PR | 2.73 |
| Calls for 15 fixtures | 41 |
| Input tokens per PR | 3079.5 |
| Output tokens per PR | 2463.9 |
| Mean latency per PR | 10.96 s |
| P95 latency per PR | 18.41 s |
| Wall clock for one sweep | about 2.7 min |

Dry run output (`--dry-run`, no tokens spent, prices set to 0.28 / 0.42 USD per million):

```
planned LLM calls : about 41 for one strategy
expected wall time: about 2.7 min per full sweep
cost              : estimated USD 0.0285 per run at the configured price
```

The price is a configured parameter, not an invoice. No billed amount is claimed.

## 8. Why the offline mode cannot substitute for B and C

`--mode offline` replaces the agents with `OfflineFixtureRunner`, a deterministic regex
runner that ignores the prompt. Its output therefore says nothing about prompt v2, and
an offline B/C comparison would only exercise the verifier against regex findings. It
validates the harness, not the model.

The shared-input requirement is solved differently: `evals/ablation.py` makes **one**
model pass and freezes the output before any verification, then replays the same
findings through both verifier configurations. No second model call, so no model
nondeterminism can enter the B/C comparison.

## 9. Limitations

1. **15 fixtures.** Each labelled case holds exactly one defect. A correct but
   unlabelled observation is still charged as a false positive, which caps the
   achievable precision. Expanding to 50+ multi-defect cases is stage 3 work that
   has not been done.
2. **The replay is a single recording.** It cannot capture run-to-run variance, and
   it cannot re-derive the 19 verifier rejections.
3. **The unified matcher's text channel is language sensitive by construction.** It
   only matters for output that lacks `issue_type`, which after prompt v2 should be
   nothing, but any future relabelling to `other` will silently lose recall.
4. **B and C are unmeasured.** No claim about whether prompt v2 or the evidence-aware
   verifier improves real precision is made in this document.
5. **Layer 2 is syntax-level.** It proves a construct exists or is absent at the
   reported call site in one file. It cannot follow data across functions or modules,
   which is why cross-file issues return `UNKNOWN` rather than a verdict.
6. **Residual false-rejection risk in layer 2.** Auditing the checks found and fixed
   three ways a real defect could be rejected: another call site in the same function,
   a bounds guard that did not dominate the read, and `subprocess.run(cmd, **opts)`
   being cleared even though `**opts` may carry `shell=True` (now `UNKNOWN`). The
   remaining known gaps are patterns the checks do not model, and those return
   `UNKNOWN` rather than `CONTRADICTED`:
   - SQL built through helper functions or class attributes
   - `NullType` guards written as `if not isinstance(user, X)` (a false positive, not a
     false rejection)
   - shell invocation through a wrapper the checker does not recognise
   A wrong `SUPPORTED` shows up as a false positive; a wrong `CONTRADICTED` would
   remove a real defect, so the checks are written to prefer `UNKNOWN`.

## 10. The B/C harness

`evals/ablation.py` implements the shared-input requirement:

1. One pass calls the model (or the deterministic runner) and the flow freezes
   `state.raw_findings` before its own verification.
2. Rung B replays those frozen findings with `check_conditions=False`.
3. Rung C replays the *same* frozen findings with `check_conditions=True`.
4. Both rungs are scored by the same evaluator against the same ground truth.

A single model pass is therefore enough, and the only difference between B and C is
layer 2. Rung A is not part of this harness: it is the archived prompt-v1 output, which
differs in prompt *and* output schema, so it is re-scored separately by
`evals.replay_v1`. Mistaking A→B for a prompt-only improvement would be wrong, and the
harness refuses to imply it by keeping A out.

Offline exercise of the harness (deterministic runner, no model):

| Rung | P | R | F1 | Strict F1 | TP | FP | FN | Rejections | Advisories |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A-raw (no verifier) | 1.000 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 | 0 | 0 |
| B (layer 1) | 1.000 | 1.000 | 1.000 | 1.000 | 10 | 0 | 0 | 0 | 0 |
| C (layer 1 + 2) | 1.000 | 0.900 | 0.947 | 0.900 | 9 | 0 | 1 | 0 | 1 |

This only proves the plumbing: the offline runner emits no false claims, so layer 2 has
nothing to reject and only the `test-gap-07` demotion shows up. It is **not** evidence
about layer 2's effect on real model output. `test_ablation_replays_the_same_raw_findings_for_both_rungs`
covers the mechanism with a synthetic false SQL claim: B keeps it (`fp=1`), C rejects
it (`condition_not_met`, `fp=0`), and the shared input is byte-identical afterwards.

## 11. Is a critic worth adding?

Not yet, and the current data argues against it:

* Layer 2 already rejects or demotes every claim it can decide; the undecided
  remainder is `UNKNOWN`, which is exactly the population a critic would review.
* On the recorded v1 output there are 74 false positives and 1 true positive under
  the unified rule. A critic reviewing that population would need to be very accurate
  to help, and it would add a call per undecided finding, roughly doubling cost.
* The measurable bottleneck is not adjudication quality but **coverage**: 34 of 75
  findings had no line number at all, and none carried a classification. Those are
  prompt and schema problems, already addressed by prompt v2, and their effect is
  what B/C should measure first.

Recommendation: run B and C, and only consider a critic if C's advisory rate is high
while its defect recall is already acceptable.

## 12. Next command, pending budget approval

```bash
# plan only, no tokens
PYTHONPATH=src .venv/bin/python -m evals.ablation --mode online --dry-run

# one model pass, both verifier rungs, one report
PYTHONPATH=src .venv/bin/python -m evals.ablation --mode online \
  --model deepseek/deepseek-chat --output artifacts/evals/ablation
```

Measured dry-run output:

```
model calls     : about 41 (one pass; both rungs reuse the same raw findings)
verifier passes : 2 (condition checks off, then on)
estimated cost  : USD 0.0285 at the configured price
```

For variance, run the whole harness three times and report the spread. Each repetition
costs one model pass, not two, because both rungs share the findngs.

Artifacts written by a real run: `ablation.json` holding the per-rung scores, the
frozen `raw_findings` for every case, the verified findings for each rung, verifier
rejection reasons per type, token usage, call counts and latency. Nothing is
overwritten: each run gets its own UTC timestamp directory.
