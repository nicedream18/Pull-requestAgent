# Benchmark Record

Every record below states the evaluator version that produced it. Numbers from different evaluator
versions are not comparable, and the `legacy_*` keys exist only to reproduce older records.

The checked-in benchmark command is the source of truth for current measurements. Running it creates a timestamped report and records model, prompt version, dataset version, date, tokens, latency, calls, failures, and findings.

The offline record below was produced by `prguard-eval-v2` with condition checks enabled:

| Strategy | Precision | Recall | F1 | Strict P | Strict R | Strict F1 | TP | FP | FN | Advisory rate | Findings/PR | FP per clean PR | Input tok/PR | Calls/PR | Mean s | P95 s | Est. USD | Agent fail rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| single | 1.000 | 0.500 | 0.667 | 1.000 | 0.500 | 0.667 | 5 | 0 | 5 | 0.000 | 0.33 | 0.00 | 42.6 | 1.00 | 0.000 | 0.001 | n/a | 0.000 |
| static | 1.000 | 0.900 | 0.947 | 0.900 | 0.900 | 0.900 | 9 | 0 | 1 | 0.100 | 0.67 | 0.00 | 170.4 | 4.00 | 0.000 | 0.001 | n/a | 0.000 |
| dynamic | 1.000 | 0.900 | 0.947 | 0.900 | 0.900 | 0.900 | 9 | 0 | 1 | 0.100 | 0.67 | 0.00 | 117.1 | 2.73 | 0.000 | 0.001 | n/a | 0.000 |


`dynamic` and `static` each confirm 9 of the 10 labelled defects. The single miss is
`test-gap-07` (`missing_test_coverage`): a diff cannot prove that no test exists, so the
verifier returns `UNKNOWN`, keeps the finding as an advisory flagged for human review, and
`advisory_rate` records the cost. That is a deliberate recall-for-honesty trade, not a
regression, and `docs/ablation-results.md` §4 states it as such.

Single and static strategies are evaluated with the same evaluator, so the comparison is
like-for-like. Single confirms 5 of 10 because one agent only covers its own scope.

## Online LLM record (prompt v2, current code)

One valid run, 2026-10-24T10:22:47Z, `deepseek/deepseek-chat`, evaluator `prguard-eval-v2`,
15 fixtures, one model pass with both verifier rungs replayed on the same findings:

| Rung | P | R | F1 | Strict F1 | TP | FP | FN | Rejections |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before verification | 0.160 | 0.400 | 0.229 | 0.211 | 4 | 21 | 6 | 0 |
| layer 1 | 0.667 | 0.400 | 0.500 | 0.471 | 4 | 2 | 6 | 21 |
| layer 1 + 2 | 0.667 | 0.400 | 0.500 | 0.471 | 4 | 2 | 6 | 21 |

21 of 21 rejections were `invalid_path`. 72,197 input and 20,768 output tokens, 41 calls,
8.61 s mean and 16.74 s P95 per case, USD 0.0289 at the configured price. Full analysis:
[ablation-results](ablation-results.md) §3.

## Online LLM record (prompt v1, historical)

> **Status:** this record belongs to prompt `prguard-review-v1` with the layer-1-only verifier and
> the pre-`prguard-eval-v2` matcher. Its 0.093 / 0.700 / 0.165 is reproduced exactly by the frozen
> `legacy_*` keys and by `evals.replay_v1`; the same recording scores 0.013 / 0.100 / 0.024 under the
> unified rule. See [ablation-results](ablation-results.md) before quoting either. The
> current branch ships `prguard-review-v2`, which adds the issue-type taxonomy, defect/advisory
> separation, abstention and AST condition checks. The numbers below are the *baseline* for the
> ablation in `docs/false-positive-analysis.md`; they are not what the current code produces. A fresh
> online run has not been executed because it has not been budgeted.

An online run was executed on October 24, 2026 with `deepseek/deepseek-chat` and prompt `prguard-review-v1` over the same 15 fixtures (`mode=online`, `evaluation_type=live_llm_on_local_fixtures`):

| Strategy | Precision | Recall | F1 | TP | FP | FN | Input tok/PR | Calls/PR | Mean s | P95 s | Agent fail rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dynamic | 0.093 | 0.700 | 0.165 | 7 | 68 | 3 | 3079.5 | 2.73 | 10.96 | 18.41 | 0.000 |

The live model produces far more findings than the fixture labels describe: 75 verified findings for 10 labeled defects, plus 19 rejected by the verifier (20.2% of 94 raw findings). Recall is 0.700; the three missed categories are `missing_exception_handling`, `missing_test_coverage` and `overly_permissive_file_mode`.

False positives by category: `testing` 30, `bug` 13, `quality` 12, `security` 9, `performance` 4. Precision by agent: `CodeQualityAgent` 6/30 (0.20), `TestImpactAgent` 1/39 (0.03), `SecurityAgent` 0/4, `ContextAgent` 0/2.

Two caveats apply to these numbers:

1. The fixtures hold exactly one labeled defect each, so a correct-but-unlabeled observation still counts as a false positive. This metric is label-restricted precision, not real-world precision. The no-defect fixtures contain no tests at all, which is why `TestImpactAgent` reports "missing test coverage" on them and inflates `false_positives_per_no_defect_pr` to 4.6.
2. `Full ctx tok/PR` and `Opt ctx tok/PR` (84.0 and 42.6) measure the review context string only, not the full prompt. The reported `Input tok/PR` of 3079.5 includes system prompts and is the number to quote for cost. The 49.3% context reduction does not translate into a 49.3% token-cost reduction.

`estimated_api_cost_usd` is `n/a` because no price was configured for this model; set `PRGUARD_INPUT_PRICE_PER_1M` and `PRGUARD_OUTPUT_PRICE_PER_1M` to record it.
