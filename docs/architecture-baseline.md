# Architecture Baseline

## Source snapshot

The repository started from the CrewAI GitHub pull request review template in the upstream history (`93b45ad` and earlier commits). The original package is retained under `src/code_review_crew_for_github_pull_request/` so its behavior and provenance remain visible.

The baseline had two agents (`quick_review_assistant` and `code_review_expert`) and four sequential tasks: fetch and analyze the patch, search related PRs, search related files, and generate a Markdown comment. The original crew used `Process.sequential` and a `before_kickoff` hook; it did not have a Flow state, deterministic routing, structured Finding output, a verifier, an evaluation dataset, or automated tests.

The reproducible environment currently resolves CrewAI `0.98.0`, PyGithub `2.5.0`, Pydantic `2.10.5`, FastAPI `0.115.5`, and pytest `9.1.1` on Python 3.13.9. CrewAI was kept at the existing pinned version; FastAPI, Uvicorn, and pytest were added as direct project dependencies for the new API and test surface.

## Baseline risks found

- The entry point contained a fixed repository and pull request URL.
- The original patch tool accepted arbitrary URLs, did not set a timeout, and had no response-size bound.
- GitHub errors were returned as untyped strings; 401, 403, 404, and rate limits were not distinguished.
- Related file discovery called a placeholder `_analyze_file_relations` implementation.
- The generated review was a string template with a placeholder recommendation rather than evidence-checked findings.
- Agent output was free-form, and file paths or line numbers were not validated.
- There were no reproducible fixtures, metrics, tests, trace output, or failure isolation.
- The repository did not contain a LICENSE file. Distribution should preserve the upstream attribution and confirm the upstream license before publishing a derivative.

## Planned and implemented delta

PRGuard adds a separate `src/prguard/` package. Its `ReviewFlow` uses CrewAI `@start`, `@router`, and `@listen` with a Pydantic state. A read-only GitHub REST client feeds deterministic risk classification, Python AST context retrieval, bounded specialist Crews, evidence verification, and JSON/Markdown/trace reports. `evals/` adds 15 local ground-truth fixtures, offline replay, online entry points, and Single/Static/Dynamic comparison metrics.
