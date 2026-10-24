from __future__ import annotations

import asyncio
import logging
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from crewai.flow.flow import Flow, listen, router, start

from prguard.agents import AGENTS, PROMPT_VERSION, configured_model, run_agent
from prguard.context import build_context_bundle, related_python_paths
from prguard.github import GitHubClient
from prguard.models import AgentRun, FlowState, PRReviewRequest, PRSnapshot, RiskLevel
from prguard.reports import write_reports
from prguard.verifier import verify_findings

logger = logging.getLogger("prguard.flow")


def classify_risk(snapshot: PRSnapshot) -> RiskLevel:
    changed = {item.path.lower() for item in snapshot.files}
    total_changes = sum(item.changes for item in snapshot.files)
    security_tokens = ("auth", "permission", "token", "secret", "password", "crypto", "payment", "sql", "query", "shell", "exec")
    if total_changes >= 600 or len(changed) >= 40:
        return RiskLevel.LARGE
    if any(any(token in path for token in security_tokens) for path in changed):
        return RiskLevel.HIGH
    diff_lower = snapshot.diff.lower()
    if any(token in diff_lower for token in ("eval(", "exec(", "subprocess", "cursor.execute", "password", "secret", "chmod", "api_token", "api-key", "api key")):
        return RiskLevel.HIGH
    if len(changed) > 8 or total_changes >= 150:
        return RiskLevel.HIGH
    if all(path.endswith((".md", ".rst", ".txt")) for path in changed) and changed:
        return RiskLevel.LOW
    if len(changed) > 2 or total_changes >= 40:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def route_agents(snapshot: PRSnapshot, risk: RiskLevel, strategy: str) -> list[str]:
    if strategy == "single":
        return ["CodeQualityAgent"]
    if strategy == "static":
        return list(AGENTS)
    selected = ["CodeQualityAgent"]
    docs_only = bool(snapshot.files) and all(item.path.lower().endswith((".md", ".rst", ".txt")) for item in snapshot.files)
    if not docs_only:
        selected.append("TestImpactAgent")
    if risk in {RiskLevel.HIGH, RiskLevel.LARGE}:
        selected.extend(["SecurityAgent", "ContextAgent"])
    return selected


class ReviewFlow(Flow[FlowState]):
    """CrewAI Flow routes PR review requests into bounded specialist Crews."""

    def __init__(
        self,
        github_client: GitHubClient | None = None,
        agent_runner: Callable | None = None,
        event_sink: Callable[[dict], None] | None = None,
    ):
        super().__init__()
        self.github_client = github_client or GitHubClient()
        self.agent_runner = agent_runner or run_agent
        self.event_sink = event_sink
        self.snapshot: PRSnapshot | None = None
        self.source_map: dict[str, str] = {}
        self.related_sources: dict[str, str] = {}
        self.related_paths: set[str] = set()
        self.related_history: list[dict[str, str | int]] = []
        self.context = ""
        self.request: PRReviewRequest | None = None
        self._flow_started = 0.0

    def _trace(self, node: str, status: str, started: float, **metadata: str | int | float) -> None:
        event: dict[str, str | int | float] = {
            "node": node,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(time.monotonic() - started, 4),
            **metadata,
        }
        self.state.trace_events.append(event)
        logger.info(json.dumps(event, sort_keys=True))
        if self.event_sink:
            try:
                self.event_sink({
                    "node_id": node,
                    "event_type": "node",
                    "status": status,
                    "summary": f"{node} {status}",
                    "metrics": {key: value for key, value in metadata.items()},
                    **event,
                })
            except Exception:
                logger.debug("event sink failed", exc_info=True)

    @start()
    def initialize(self) -> dict:
        started = time.monotonic()
        self.state.review_status = "running"
        self._flow_started = time.monotonic()
        self.state.run_id = str(uuid.uuid4())
        self.request = PRReviewRequest.model_validate(self.state.request)
        self.state.repo_name = self.request.repo or ""
        self.state.pr_number = self.request.pr_number or 0
        self._trace("initialize", "success", started, run_id=self.state.run_id)
        return {"repo": self.state.repo_name, "pr": self.state.pr_number}

    @listen("initialize")
    def fetch_and_prepare(self, _: dict) -> str:
        started = time.monotonic()
        try:
            assert self.request is not None
            self.snapshot = self.github_client.fetch_pr(self.request)
            self.state.head_sha = self.snapshot.head_sha
            self.state.changed_files = [item.path for item in self.snapshot.files]
            self.state.diff = self.snapshot.diff
            self.state.risk_level = classify_risk(self.snapshot)
            tree = self.github_client.fetch_tree(self.snapshot.repo, self.snapshot.head_sha)
            changed_sources: dict[str, str] = {}
            for item in self.snapshot.files:
                if item.status != "removed" and item.path.endswith(".py"):
                    content = self.github_client.fetch_file(self.snapshot.repo, item.path, self.snapshot.head_sha)
                    if content is not None:
                        changed_sources[item.path] = content
            relevant = related_python_paths(self.state.changed_files, changed_sources, tree)
            for path in relevant[:8]:
                content = self.github_client.fetch_file(self.snapshot.repo, path, self.snapshot.head_sha)
                if content is not None:
                    self.related_sources[path] = content
            self.source_map = {**changed_sources, **self.related_sources}
            self.related_paths = set(self.related_sources)
            find_history = getattr(self.github_client, "find_related_prs", None)
            if callable(find_history):
                self.related_history = find_history(self.snapshot.repo, self.state.changed_files, self.snapshot.pr_number)
                self.state.historical_prs = self.related_history
            bundle = build_context_bundle(self.snapshot, changed_sources, self.related_sources)
            history_context = "\n".join(
                f"Historical PR context (untrusted metadata): #{item['number']} {item['title']} {item['url']}"
                for item in self.related_history
            )
            self.context = f"{bundle.optimized_context}\n\n{history_context}" if history_context else bundle.optimized_context
            self.state.metrics.update({
                "input_tokens": bundle.optimized_tokens,
                "full_context_tokens": bundle.full_tokens,
                "context_tokens_saved": max(0, bundle.full_tokens - bundle.optimized_tokens),
                "context_files_selected": len(bundle.selected_context_paths),
            })
            if not self.snapshot.diff_complete:
                self.state.errors.append({"type": "diff_incomplete", "message": "GitHub PR file API omitted patch data for some changed files; findings are limited to returned patches."})
            result = "prepared"
            self._trace("fetch_and_prepare", "success", started, changed_files=len(self.snapshot.files), risk=self.state.risk_level.value)
        except Exception as exc:
            self.state.errors.append({"type": type(exc).__name__, "message": str(exc)[:240]})
            self.state.review_status = "failed"
            result = "fetch_failed"
            self._trace("fetch_and_prepare", "failed", started, error_type=type(exc).__name__)
        return result

    @router("fetch_and_prepare")
    def route_review(self) -> str:
        started = time.monotonic()
        if self.state.review_status == "failed":
            return "route_failed"
        assert self.snapshot is not None and self.request is not None
        self.state.selected_agents = route_agents(self.snapshot, self.state.risk_level, self.request.strategy)
        self._trace("route_review", "success", started, selected_agents=len(self.state.selected_agents))
        return {"single": "run_single", "static": "run_static", "dynamic": "run_dynamic"}[self.request.strategy]

    async def _run_selected(self) -> list[AgentRun]:
        assert self.request is not None
        if len(self.state.selected_agents) > self.request.max_agent_calls:
            self.state.selected_agents = self.state.selected_agents[:self.request.max_agent_calls]
        semaphore = asyncio.Semaphore(self.request.concurrency)
        async def run_one(name: str) -> AgentRun:
            if self.event_sink:
                try:
                    self.event_sink({
                        "node_id": name,
                        "event_type": "agent",
                        "status": "running",
                        "summary": f"{name} started",
                        "metrics": {},
                    })
                except Exception:
                    logger.debug("event sink failed", exc_info=True)
            result = await self.agent_runner(
                name,
                self.context,
                self.request.model,
                semaphore=semaphore,
                timeout_seconds=self.request.timeout_seconds,
            )
            if self.event_sink:
                try:
                    self.event_sink({
                        "node_id": name,
                        "event_type": "agent",
                        "status": result.status,
                        "summary": f"{name} {result.status}",
                        "metrics": {
                            "duration_seconds": result.duration_seconds,
                            "calls": result.calls,
                            "input_tokens": result.input_tokens,
                            "output_tokens": result.output_tokens,
                            "finding_count": len(result.findings),
                        },
                        "error": result.error,
                    })
                except Exception:
                    logger.debug("event sink failed", exc_info=True)
            return result

        jobs = [run_one(name) for name in self.state.selected_agents]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        runs: list[AgentRun] = []
        for name, result in zip(self.state.selected_agents, results):
            if isinstance(result, AgentRun):
                runs.append(result)
                continue
            now = datetime.now(timezone.utc).isoformat()
            runs.append(AgentRun(
                agent=name,
                status="failed",
                started_at=now,
                ended_at=now,
                error_type=type(result).__name__,
                error="Agent runner raised an exception",
            ))
        return runs

    async def _run_strategy(self, strategy: str) -> str:
        started = time.monotonic()
        try:
            runs = await self._run_selected()
            self.state.agent_runs.extend(runs)
            for run in runs:
                self.state.findings.extend(run.findings)
                self.state.token_usage["input"] += run.input_tokens
                self.state.token_usage["output"] += run.output_tokens
                self.state.token_usage["total"] += run.total_tokens
                self.state.token_usage["calls"] += run.calls
                if run.status != "success":
                    self.state.errors.append({
                        "type": run.error_type or run.status,
                        "message": f"{run.agent}: {run.error or 'did not complete successfully'}",
                    })
            failed = sum(run.status != "success" for run in runs)
            self.state.review_status = "partial" if failed else "running"
            self._trace(f"{strategy}_agents", "partial" if failed else "success", started, agents=len(runs), failures=failed)
            return f"{strategy}_done"
        except Exception as exc:
            self.state.errors.append({"type": type(exc).__name__, "message": "Agent scheduling failed"})
            self.state.review_status = "partial"
            self._trace(f"{strategy}_agents", "failed", started, error_type=type(exc).__name__)
            return f"{strategy}_done"

    @listen("run_single")
    async def execute_single_path(self, _: str) -> str:
        return await self._run_strategy("single")

    @listen("run_static")
    async def execute_static_path(self, _: str) -> str:
        return await self._run_strategy("static")

    @listen("run_dynamic")
    async def execute_dynamic_path(self, _: str) -> str:
        return await self._run_strategy("dynamic")

    @listen({"type": "OR", "methods": ["execute_single_path", "execute_static_path", "execute_dynamic_path", "route_failed"]})
    def verify_and_report(self, _: str) -> dict:
        started = time.monotonic()
        if self.snapshot is None or self.request is None:
            return {"run_id": self.state.run_id, "status": "failed"}
        verified, rejected = verify_findings(self.state.findings, self.snapshot, self.source_map, self.related_paths)
        self.state.verified_findings = verified
        for error in rejected:
            self.state.errors.append(error)
        if not self.state.errors:
            self.state.review_status = "completed"
        elif self.state.review_status != "failed":
            self.state.review_status = "partial"
        self._trace("verify_and_report", "success", started, verified_findings=len(verified), rejected_findings=len(rejected))
        self.state.execution_time = time.monotonic() - self._flow_started
        self.state.metrics["execution_seconds"] = self.state.execution_time
        defects = [item for item in verified if item.finding_type.value == "defect"]
        self.state.metrics["raw_findings"] = len(self.state.findings)
        self.state.metrics["verified_findings"] = len(verified)
        self.state.metrics["rejected_findings"] = len(rejected)
        self.state.metrics["defect_findings"] = len(defects)
        self.state.metrics["advisory_findings"] = len(verified) - len(defects)
        self.state.metrics["needs_human_review"] = sum(1 for item in verified if item.needs_human_review)
        self.state.metrics["agent_failure_rate"] = (
            sum(run.status != "success" for run in self.state.agent_runs) / len(self.state.agent_runs)
            if self.state.agent_runs else 0
        )
        write_reports(
            self.state,
            self.snapshot,
            self.request.output_dir,
            self.source_map,
            PROMPT_VERSION,
            model=configured_model(self.request.model),
            execution_mode=self.request.mode,
        )
        return {"run_id": self.state.run_id, "status": self.state.review_status, "findings": len(verified)}
