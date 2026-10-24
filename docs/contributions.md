# Contributions and Provenance

## Upstream template behavior retained

- The original package path and CrewAI template entry points remain under `src/code_review_crew_for_github_pull_request/`.
- The original YAML agent/task configuration, Streamlit demo, and GitHub helper history remain available for comparison.
- Existing uncommitted upstream-derived edits were preserved rather than rewritten as original work.

## PRGuard implementation

- `src/prguard/models.py`: typed request, Flow state, Finding, snapshot, and execution schemas.
- `src/prguard/github.py`: bounded, read-only GitHub REST access with typed HTTP errors.
- `src/prguard/context.py`: Python AST import retrieval, Top-K context selection, budgets, and content-hash deduplication.
- `src/prguard/flow.py`: CrewAI Flow start/router/listen orchestration, deterministic risk routing, bounded parallel agents, and report generation.
- `src/prguard/agents.py`: CodeQuality, Security, TestImpact, and Context specialist Crews with structured output and bounded retry.
- `src/prguard/verifier.py`: path, line, evidence, confidence, and duplicate validation.
- `evals/`: 15 checked-in fixtures, offline runner, online entry point, metrics, CSV/JSON/Markdown/SVG outputs.
- `tests/`: validation, GitHub error handling, routing, verifier, metrics, offline runner, and API tests.
- `evals/import_reviews.py`: append-only import of human review decisions, identity/time/version validation, conflict detection, clean-case coverage, and progress reporting; `evals.run` and `evals.ablation` block candidate-set scoring until review is complete.
- `evals/ablation.py`: records prompt SHA, shared context and post-processing configuration, raw path validity, and per-case API/timeout/parse/agent failures for later v2/v3 comparison.
- `docs/review/` and `tests/test_review_import.py`: human-editable audit sheet, review protocol, current progress, and importer safeguards. Code never generates reviewer identity or approval.

The independent 36-case candidate dataset is not yet human-reviewed. The 15 development fixtures are pipeline regressions only and must not be reported as independent test or holdout measurements.

## License note

No LICENSE file was present in the source checkout used for this work. The upstream source and attribution should be retained, and the repository owner should confirm the upstream CrewAI template license before redistributing a derivative project.
