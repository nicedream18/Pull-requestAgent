# Evaluation Protocol (frozen 2026-10-24)

Anything compared across runs must be recorded here and must not be changed between the
rungs of one comparison. If a value changes, every earlier result is re-scored and the old
numbers are republished alongside the new ones, never replaced.

## 1. Frozen inputs

| Item | Value |
| --- | --- |
| Evaluator | `prguard-eval-v2` (`EVALUATOR_VERSION` in `evals/metrics.py`) |
| Legacy matcher | frozen inside `_legacy_score`; guarded by `tests/test_evaluator_v1_replay.py` |
| Prompt under test | `prguard-review-v3`, sha `c6166a6c6edd` |
| Previous prompt (baseline) | `prguard-review-v2`, sha `044658d206ef`, restored from commit `dd6de21` |
| Model | `deepseek/deepseek-chat` |
| Model parameters | temperature and max_tokens left at provider defaults; no sampling override is sent |
| Development dataset | `evals/datasets/prguard_v1.json` v1.0, 15 cases |
| Candidate dataset | `evals/datasets/prguard_v2.json` v2.1-candidate, 36 cases |
| Review vocabulary | `draft` / `reviewed` / `needs_revision` / `indeterminate` |
| Frozen online output | `evals/frozen/online_20261024T102247Z.json` |
| Git commit | recorded per run by `evals.run` and `evals.ablation` (`git_commit` field) |
| Concurrency / timeout / call cap | 3 / 90 s / 6 per PR |

## 2. Dataset roles

| Dataset | Role | May be used for a generalisation claim? |
| --- | --- | --- |
| `prguard_v1.json` (15) | development and regression only; it has been tuned against five times | **no** |
| `prguard_v2.json` dev split (12) | tuning | no |
| `prguard_v2.json` test split (17) | scoring, inspected once per candidate change | only with the label caveat below |
| `prguard_v2.json` holdout split (7) | one final measurement, when the pipeline is frozen | yes, once |
| `evals/frozen/*.json` | replay of one historical model run | no; it is a regression fixture |

**Label caveat.** Every case in `prguard_v2.json` is `draft` or `indeterminate`, and
`human_reviewed_count` is `0`. No label was produced by a model. Until a second person
reviews them, results on this set are *pipeline measurements against author-written
drafts*, not benchmark scores. The dataset file states this in `label_policy`, and
`evals.check_labels --require-reviewed` refuses to run while drafts remain.

## 3. Metric definitions

All scorers use one-to-one greedy matching; a prediction can satisfy at most one labelled
defect, and a labelled defect can be satisfied by at most one prediction.

| Metric | Definition |
| --- | --- |
| `defect_precision` | TP / (TP + FP), advisories excluded from the denominator |
| `defect_recall` | TP / (TP + FN) over all labelled defects |
| `f1_score` | harmonic mean of the two above |
| `strict_precision` / `strict_f1` | same, with every advisory counted as a false positive |
| `false_positives_per_no_defect_pr` | false positives on clean cases / number of clean cases |
| `category_recall` | recall per `issue_type` |
| path recovery accuracy | correct recoveries / recoveries, requires reviewed labels; **not computable on draft data** |
| `category_stats` | per `issue_type`: tp, fp, fn, labelled, precision, recall, f1 |

Per-category accounting invariant: for every category, `tp + fn == labelled`, and
`sum(tp)` / `sum(fn)` / `sum(fp)` over categories equals the global `tp` / `fn` / `fp`.
`tests/test_category_metrics.py` pins this with hand-computed counts.

A match requires all of: `finding_type == defect`, same file, a concrete `start_line`
within two lines of the label, evidence present verbatim in that file, and either
`issue_type` equality or the text fallback (same `category`, overlap >= 0.20).

`advisory` findings never count as detections. `needs_human_review` is reported but does
not change the score.

## 4. What each comparison isolates

| Comparison | Changes | Valid claim |
| --- | --- | --- |
| v1 output, legacy vs unified matcher | measurement only | no quality claim; it shows what the old scorer over-credited |
| v1 output, unified, before vs after path recovery | post-processing only | effect of `PathResolver` on fixed model output |
| v2 vs v3 prompt, same dataset and model | prompt only | effect of the prompt, **provided** the same post-processing is applied to both |
| layer 1 vs layer 1+2 on the same frozen findings | verifier only | effect of layer 2 |

The v2 vs v3 prompt comparison is only valid when both runs use the same dataset, model,
parameters, and the same `resolve_paths` / `dedupe` / `check_conditions` settings. Comparing
a v3 run with recovery enabled against the historical v2 numbers *without* recovery is not
a prompt comparison and must not be described as one.

## 4b. Prompt selection is real, not a label

`prguard/agents.py` keeps prompt bodies in `PROMPT_BODIES`, keyed by version. The CLI
flag `--prompt-version` on `evals.run` and `evals.ablation` selects one, it is carried by
`PRReviewRequest.prompt_version`, and `run_agent_once` renders it into the actual CrewAI
`Task`. `prompt_sha()` fingerprints the body and is printed by every dry run.

`--output artifacts/evals/prompt-v2` is only a directory name and proves nothing; the
version in `results.json` / `ablation.json` and the printed sha are the evidence.

Reference shas: `prguard-review-v2` = `044658d206ef`, `prguard-review-v3` = `c6166a6c6edd`.

## 5. Procedure for a paid comparison

1. `pytest`, `ruff`, `evals.check_labels`, offline benchmark, offline ablation all green.
2. Record `git rev-parse HEAD`.
3. Dry run to confirm call count and estimated cost.
4. Run v2 prompt and v3 prompt separately, each one model pass, each into its own
   timestamped directory. Do not overwrite:

   ```bash
   PYTHONPATH=src .venv/bin/python -m evals.ablation --mode online \
     --prompt-version prguard-review-v2 --output artifacts/evals/prompt-v2
   PYTHONPATH=src .venv/bin/python -m evals.ablation --mode online \
     --prompt-version prguard-review-v3 --output artifacts/evals/prompt-v3
   ```
5. Score both with the same evaluator and the same post-processing flags.
6. Report: TP/FP/FN, precision, recall, F1, strict F1, findings per PR, rejections by
   reason, tokens, calls, latency, cost. Report both runs in full, including any rung where
   the new prompt is worse.
7. If a rule is changed because of a test-split result, move that case to dev and say so.

## 6. Reproducing the frozen numbers without spending anything

```bash
PYTHONPATH=src .venv/bin/python -m evals.path_ablation
PYTHONPATH=src .venv/bin/python -m evals.replay_v1 \
  --input evals/frozen/online_20261024T081509Z_results.json   # v1 run, if present locally
PYTHONPATH=src .venv/bin/python -m evals.ablation --mode offline
PYTHONPATH=src .venv/bin/python -m evals.run --mode offline
```
