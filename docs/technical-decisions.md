# Technical Decisions

## CrewAI version

The implementation targets the installed CrewAI `0.98.0` from the existing project lock. The Flow decorators and asynchronous Crew kickoff were inspected against that version. No full CrewAI upgrade was performed.

## Deterministic routing first

Risk classification uses changed-file count, change count, file names, and security-sensitive code tokens. This keeps security checks explainable and prevents an LLM from silently bypassing them. A future router can be benchmarked against this deterministic baseline.

## Read-only GitHub access

`GITHUB_TOKEN_CODEREVIEW` is optional for offline fixtures and required for private or rate-limited online reviews. The client uses only `https://api.github.com`, rejects redirects, sets bounded connect/read timeouts, paginates changed files, and never posts comments.

## Context engineering

The full-context baseline keeps all returned Diff and retrieved source. The optimized bundle always preserves the Diff, then selects up to five related Python files using AST imports, test naming, module names, content overlap, a per-file character budget, and SHA-256 duplicate removal. Token counts are approximate offline counts unless the LLM returns usage metadata.

## Evidence over prose

The `Finding` schema requires category, severity, file, line range, evidence, suggestion, confidence, and source agent. The verifier rejects missing paths, impossible line numbers, and evidence that is not present near the cited source. Low-confidence findings are marked for human review.

## Reproducibility

The offline runner uses fixed local fixtures and deterministic rules. Its metrics are labeled as pipeline and routing checks, not model quality. Online runs require an API key and record model, prompt version, date, tokens, latency, calls, failures, and estimated cost when price environment variables are provided.

## Prospective model configuration

The frozen October 24, 2026 run used the then-configured `deepseek/deepseek-chat`; that historical artifact is unchanged. Current local defaults use `deepseek/deepseek-flash` and the official `https://api.deepseek.com` base URL. Prompt v2/v3 test runs remain blocked until all 36 candidate cases, including clean cases, are human-reviewed. Temperature and `top_p` are still provider defaults, so the prospective experiment does not yet have a complete sampling-parameter freeze.
