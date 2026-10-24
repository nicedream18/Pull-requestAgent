# Experiment Design and Cost

The goal is to show that unverifiable review comments go down without losing defect recall, and to do
it in a way someone else can re-run. This document records the design, the switches, and the cost.

## Rungs

| Rung | Configuration | How to run | Status |
| --- | --- | --- | --- |
| A | Dynamic multi-agent, prompt `prguard-review-v1`, layer-1 verifier | checkout `fcb4138`, then `--mode online --strategy dynamic` | **measured** (15 fixtures, see `docs/benchmark.md`) |
| B | A + prompt `prguard-review-v2` (taxonomy, defect/advisory, abstention) | current branch, `--condition-checks off` | runnable, not measured online |
| C | B + evidence-aware verifier layer 2 | current branch, `--condition-checks on` (default) | runnable, not measured online |
| D | C + conditional critic | — | **not implemented**; would need its own token budget |
| E | Single agent and static multi-agent on the same prompt | `--strategy single` / `--strategy static` | runnable |

A and B differ in prompt version, B and C differ only in the `condition_checks` flag, so the two
comparisons isolate one variable each. `PRReviewRequest.condition_checks` is the switch; it is also
exposed on the eval CLI.

## Commands

```bash
# B: taxonomy without condition checks
PYTHONPATH=src .venv/bin/python -m evals.run --mode online --strategy dynamic \
  --model deepseek/deepseek-chat --condition-checks off --output artifacts/evals/ablation-b

# C: taxonomy plus evidence-aware verification
PYTHONPATH=src .venv/bin/python -m evals.run --mode online --strategy dynamic \
  --model deepseek/deepseek-chat --condition-checks on --output artifacts/evals/ablation-c

# A: the recorded baseline pipeline
git worktree add /tmp/prguard-v1 fcb4138
cd /tmp/prguard-v1 && PYTHONPATH=src .venv/bin/python -m evals.run --mode online \
  --strategy dynamic --model deepseek/deepseek-chat --output /tmp/ablation-a
```

Each run writes `results.json`, `results.csv`, `benchmark.md` and `benchmark.svg`. `results.json`
records the model, prompt version, dataset version, timestamp, tokens, calls, latency, per-case
findings, verifier rejections and per-agent statistics. Add the git commit to the record by hand when
archiving, because the eval process does not shell out to git.

## Repetition and variance

A single run per rung is not enough to claim a difference on 15 fixtures. Recommended protocol, once a
budget exists:

1. Three runs per rung, same model and parameters.
2. Report the mean and the spread of precision, recall and F1, not one number.
3. Treat a change as real only when the intervals do not overlap; otherwise increase the fixture count
   first, because 15 cases cannot resolve small differences.

Set `PRGUARD_INPUT_PRICE_PER_1M` and `PRGUARD_OUTPUT_PRICE_PER_1M` to have `estimated_api_cost_usd`
filled in.

## Cost estimate

Measured consumption for the 15-fixture dynamic run (rung A, prompt v1):

| Quantity | Value |
| --- | ---: |
| LLM calls per PR | 2.73 |
| Calls for 15 fixtures | 41 |
| Input tokens per PR | 3079 |
| Input tokens per run | ~46k |
| Output tokens per PR | ~2464 |
| Output tokens per run | ~37k |

At DeepSeek list prices of roughly USD 0.28 per million input tokens and USD 0.42 per million output
tokens, one run of rung B or C costs about **USD 0.03**. Three repetitions of B and C, plus three of A,
is roughly **USD 0.27** total. The cost is dominated by wall-clock time (about 3-4 minutes per run), not
by tokens.

Rung B and C should be cheaper than A per run, because the new prompt caps output at provable findings
and the verifier rejects claims before reporting.

## What must not change between rungs

* The dataset file and its version.
* The model name and parameters.
* The fixture runner path: the same local fixtures, not live repositories.
* The metric implementation. If a rung needs a metric change, every rung must be re-scored with the
  new metric, and both the old and new scores must be published.

## Result template

Fill this in after the measurements exist. Do not invent rows.

| Rung | Prompt | Verifier | Precision | Recall | F1 | Strict precision | Advisory rate | Calls/PR | Input tok/PR | P95 latency |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | v1 | layer 1 | 0.093 | 0.700 | 0.165 | n/a | n/a | 2.73 | 3079 | 18.41 s |
| B | v2 | layer 1 | pending | pending | pending | pending | pending | pending | pending | pending |
| C | v2 | layer 1+2 | pending | pending | pending | pending | pending | pending | pending | pending |
| D | v2 | layer 1+2+critic | not implemented | | | | | | | |

Rung A has no strict precision or advisory rate because prompt v1 had no advisory class, so those
cells are genuinely not applicable rather than missing.
