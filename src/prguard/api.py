from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from evals.fixtures import FixtureClient, OfflineFixtureRunner, load_dataset, snapshot_for
from prguard.agents import configured_model
from prguard.flow import ReviewFlow
from prguard.i18n import localize_finding_payload, localize_findings
from prguard.models import PRReviewRequest


app = FastAPI(title="PRGuard Dashboard API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_TERMINAL = {"completed", "partial", "failed"}


class DashboardReviewRequest(BaseModel):
    repo: str | None = None
    pr_number: int | None = Field(default=None, gt=0)
    pr_url: str | None = None
    strategy: Literal["single", "static", "dynamic"] = "dynamic"
    mode: Literal["online", "demo"] = "online"
    demo_case_id: str | None = None
    model: str | None = None
    output_dir: str | None = None
    concurrency: int = Field(default=3, ge=1, le=8)
    timeout_seconds: int = Field(default=90, ge=5, le=600)
    max_agent_calls: int = Field(default=6, ge=1, le=12)

    @model_validator(mode="after")
    def validate_mode(self) -> "DashboardReviewRequest":
        if self.mode == "demo":
            if not self.demo_case_id:
                raise ValueError("demo_case_id is required in demo mode")
            return self
        if not self.pr_url and not (self.repo and self.pr_number):
            raise ValueError("provide pr_url or both repo and pr_number")
        return self


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_event(run_id: str, event: dict) -> dict:
    with _lock:
        job = _jobs.get(run_id)
        if not job:
            return event
        record = {
            "run_id": run_id,
            "sequence": len(job["events"]) + 1,
            "timestamp": event.get("timestamp", _now()),
            "node_id": event.get("node_id", "system"),
            "event_type": event.get("event_type", "system"),
            "status": event.get("status", "running"),
            "summary": event.get("summary", ""),
            "metrics": event.get("metrics", {}),
        }
        if event.get("error"):
            record["error"] = event["error"]
        job["events"].append(record)
        return record


def _demo_case(case_id: str) -> dict:
    for case in load_dataset()["cases"]:
        if case["case_id"] == case_id:
            return case
    raise HTTPException(status_code=404, detail=f"demo case not found: {case_id}")


def _job_response(job: dict) -> dict:
    response = {key: value for key, value in job.items() if key not in {"snapshot_obj", "source_map"}}
    response["findings"] = [localize_finding_payload(item) for item in response.get("findings", [])]
    return response


def _start_dashboard_job(payload: DashboardReviewRequest) -> str:
    run_id = str(uuid.uuid4())
    output_dir = payload.output_dir or f"artifacts/dashboard/{run_id}"
    with _lock:
        _jobs[run_id] = {
            "run_id": run_id,
            "status": "queued",
            "mode": payload.mode,
            "strategy": payload.strategy,
            "demo_case_id": payload.demo_case_id,
            "request": payload.model_dump(mode="json"),
            "events": [],
            "findings": [],
            "errors": [],
            "metrics": {},
            "selected_agents": [],
            "changed_files": [],
            "diff": "",
            "trace": [],
            "output_dir": output_dir,
        }
    _append_event(run_id, {
        "node_id": "dashboard",
        "event_type": "lifecycle",
        "status": "waiting",
        "summary": "Review queued",
        "metrics": {},
    })

    def worker() -> None:
        def event_sink(event: dict) -> None:
            _append_event(run_id, event)

        with _lock:
            _jobs[run_id]["status"] = "running"
        _append_event(run_id, {
            "node_id": "dashboard",
            "event_type": "lifecycle",
            "status": "running",
            "summary": "Review started",
            "metrics": {},
        })
        try:
            if payload.mode == "demo":
                case = _demo_case(payload.demo_case_id or "")
                snapshot, sources = snapshot_for(case)
                request = PRReviewRequest(
                    repo=snapshot.repo,
                    pr_number=snapshot.pr_number,
                    strategy=payload.strategy,
                    mode="offline",
                    model="deterministic-fixture-runner",
                    output_dir=output_dir,
                    concurrency=payload.concurrency,
                    timeout_seconds=payload.timeout_seconds,
                    max_agent_calls=payload.max_agent_calls,
                )
                flow = ReviewFlow(
                    github_client=FixtureClient(snapshot, sources),
                    agent_runner=OfflineFixtureRunner(sources),
                    event_sink=event_sink,
                )
            else:
                request = PRReviewRequest(
                    repo=payload.repo,
                    pr_number=payload.pr_number,
                    pr_url=payload.pr_url,
                    strategy=payload.strategy,
                    mode="online",
                    model=payload.model,
                    output_dir=output_dir,
                    concurrency=payload.concurrency,
                    timeout_seconds=payload.timeout_seconds,
                    max_agent_calls=payload.max_agent_calls,
                )
                flow = ReviewFlow(event_sink=event_sink)

            flow.kickoff(inputs={"request": request.model_dump()})
            snapshot = flow.snapshot
            with _lock:
                _jobs[run_id].update({
                    "status": flow.state.review_status,
                    "flow_run_id": flow.state.run_id,
                    "repo": snapshot.repo if snapshot else request.repo,
                    "pr_number": snapshot.pr_number if snapshot else request.pr_number,
                    "title": snapshot.title if snapshot else "",
                    "head_sha": snapshot.head_sha if snapshot else "",
                    "risk_level": flow.state.risk_level.value,
                    "selected_agents": flow.state.selected_agents,
                    "findings": [item.model_dump(mode="json") for item in localize_findings(flow.state.verified_findings)],
                    "errors": flow.state.errors,
                    "metrics": flow.state.metrics,
                    "token_usage": flow.state.token_usage,
                    "changed_files": [item.model_dump(mode="json") for item in snapshot.files] if snapshot else [],
                    "diff": snapshot.diff if snapshot else "",
                    "trace": flow.state.trace_events,
                    "agent_runs": [item.model_dump(mode="json") for item in flow.state.agent_runs],
                    "output_dir": output_dir,
                    "model": configured_model(request.model),
                })
            _append_event(run_id, {
                "node_id": "dashboard",
                "event_type": "lifecycle",
                "status": flow.state.review_status,
                "summary": f"Review {flow.state.review_status}",
                "metrics": {"finding_count": len(flow.state.verified_findings)},
            })
        except Exception as exc:
            with _lock:
                _jobs[run_id].update({
                    "status": "failed",
                    "errors": [{"type": type(exc).__name__, "message": str(exc)[:240]}],
                })
            _append_event(run_id, {
                "node_id": "dashboard",
                "event_type": "lifecycle",
                "status": "failed",
                "summary": "Review failed",
                "metrics": {},
                "error": str(exc)[:240],
            })

    threading.Thread(target=worker, daemon=True, name=f"prguard-{run_id[:8]}").start()
    return run_id


@app.get("/api/demos")
def list_demos() -> dict:
    cases = load_dataset()["cases"]
    preferred = ["sql-injection-05", "null-deref-01", "docs-only-09"]
    selected = [case for case in cases if case["case_id"] in preferred]
    return {
        "mode": "demo",
        "description": "Offline deterministic fixtures. These results are synthetic fixtures, not live LLM quality claims.",
        "cases": [
            {
                "case_id": case["case_id"],
                "title": case["description"],
                "defect_type": case["defect_type"],
                "file_path": case["file_path"],
                "is_real_defect": case["is_real_defect"],
            }
            for case in selected
        ],
    }


@app.post("/api/reviews", status_code=202)
def create_dashboard_review(request: DashboardReviewRequest) -> dict:
    return {"run_id": _start_dashboard_job(request), "status": "queued", "mode": request.mode}


@app.get("/api/reviews/{run_id}")
def dashboard_review_status(run_id: str) -> dict:
    with _lock:
        job = _jobs.get(run_id)
        if not job:
            raise HTTPException(status_code=404, detail="review run not found")
        return _job_response(dict(job))


@app.get("/api/reviews/{run_id}/findings")
def dashboard_review_findings(run_id: str) -> dict:
    with _lock:
        job = _jobs.get(run_id)
        if not job:
            raise HTTPException(status_code=404, detail="review run not found")
        return {
            "run_id": run_id,
            "status": job["status"],
            "findings": [localize_finding_payload(item) for item in job.get("findings", [])],
        }


@app.get("/api/reviews/{run_id}/report/{report_format}")
def dashboard_review_report(run_id: str, report_format: str):
    if report_format not in {"json", "md"}:
        raise HTTPException(status_code=404, detail="unsupported report format")
    with _lock:
        job = _jobs.get(run_id)
        if not job:
            raise HTTPException(status_code=404, detail="review run not found")
        output_dir = Path(job.get("output_dir", ""))
    path = output_dir / f"review.{report_format}"
    if not path.is_file():
        raise HTTPException(status_code=409, detail="review report is not ready")
    media_type = "application/json" if report_format == "json" else "text/markdown"
    return StreamingResponse(iter([path.read_bytes()]), media_type=media_type, headers={"Content-Disposition": f'attachment; filename="review.{report_format}"'})


@app.get("/api/reviews/{run_id}/events")
def dashboard_review_events(
    run_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    with _lock:
        if run_id not in _jobs:
            raise HTTPException(status_code=404, detail="review run not found")
    cursor = max(after, int(last_event_id or 0))

    def stream() -> Iterator[str]:
        nonlocal cursor
        idle = 0
        while idle < 600:
            with _lock:
                job = _jobs.get(run_id)
                events = list(job.get("events", [])) if job else []
                status = job.get("status", "failed") if job else "failed"
            pending = [event for event in events if event["sequence"] > cursor]
            for event in pending:
                cursor = event["sequence"]
                yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            if status in _TERMINAL and not pending:
                yield f"event: terminal\ndata: {json.dumps({'run_id': run_id, 'status': status})}\n\n"
                return
            idle += 1
            yield ": keep-alive\n\n"
            time.sleep(0.5)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/benchmarks")
def list_benchmarks() -> dict:
    root = Path("artifacts/evals")
    items = []
    if root.exists():
        for directory in sorted((item for item in root.iterdir() if item.is_dir()), reverse=True):
            result_path = directory / "results.json"
            if not result_path.exists():
                continue
            data = json.loads(result_path.read_text(encoding="utf-8"))
            items.append({
                "benchmark_id": directory.name,
                "mode": data.get("mode"),
                "dataset_version": data.get("dataset_version"),
                "case_count": data.get("case_count"),
                "evaluated_at": data.get("evaluated_at"),
                "strategies": data.get("strategies", {}),
            })
    return {"benchmarks": items, "source": "artifacts/evals/results.json", "message": "Offline results are deterministic fixture pipeline outputs."}


@app.get("/api/benchmarks/{benchmark_id}")
def get_benchmark(benchmark_id: str) -> dict:
    result_path = Path("artifacts/evals") / benchmark_id / "results.json"
    if not result_path.is_file() or "/" in benchmark_id or ".." in benchmark_id:
        raise HTTPException(status_code=404, detail="benchmark not found")
    return json.loads(result_path.read_text(encoding="utf-8"))


# Backward-compatible API used by the original CLI/demo.
@app.post("/reviews", status_code=202)
def create_review(request: PRReviewRequest) -> dict:
    payload = DashboardReviewRequest(**request.model_dump(mode="json"))
    return {"run_id": _start_dashboard_job(payload), "status": "queued"}


@app.get("/reviews/{run_id}")
def review_status(run_id: str) -> dict:
    return dashboard_review_status(run_id)


@app.get("/reviews/{run_id}/result")
def review_result(run_id: str) -> dict:
    result = dashboard_review_status(run_id)
    if result.get("status") in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="review is still running")
    return result


@app.get("/reviews/{run_id}/metrics")
def review_metrics(run_id: str) -> dict:
    result = dashboard_review_status(run_id)
    return {"run_id": run_id, "status": result.get("status"), "metrics": result.get("metrics", {})}
