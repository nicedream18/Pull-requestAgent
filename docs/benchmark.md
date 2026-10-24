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
