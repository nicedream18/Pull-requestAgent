# Benchmark Record

The checked-in benchmark command is the source of truth for current measurements. Running it creates a timestamped report and records model, prompt version, dataset version, date, tokens, latency, calls, failures, and findings.

The offline run completed on October 23, 2026 with 15 fixtures and the deterministic fixture runner. The current result was:

| Strategy | Precision | Recall | F1 | Full ctx tok/PR | Optimized ctx tok/PR | Input tok/PR | Calls/PR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Single | 1.000 | 0.500 | 0.667 | 84.0 | 42.6 | 42.6 | 1.00 |
| Static | 1.000 | 1.000 | 1.000 | 84.0 | 42.6 | 170.4 | 4.00 |
| Dynamic | 1.000 | 1.000 | 1.000 | 84.0 | 42.6 | 117.1 | 2.73 |

Single found 5 of 10 labeled defects. Static and Dynamic found all 10 in this deterministic fixture set. The optimized context averaged 42.6 tokens versus 84.0 for the full-context baseline, a 49.3% reduction. Dynamic used 31.3% fewer input tokens than Static and 31.7% fewer calls per PR. These values describe the fixture rules plus routing and verification pipeline, and the sub-millisecond local latency is not representative of an online LLM run. They are not claims about LLM review quality. Run the command again after changing fixtures or rules rather than treating this record as a universal performance guarantee.

No online LLM result is recorded in this repository because no live API evaluation was authorized or required for the offline development pass.

## Online LLM record

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
