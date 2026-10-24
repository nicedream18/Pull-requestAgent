import asyncio

from evals.fixtures import FixtureClient, OfflineFixtureRunner, load_dataset, snapshot_for
from evals.metrics import evaluate_cases
from prguard.context import build_context_bundle, python_symbols, related_python_paths
from prguard.flow import ReviewFlow, classify_risk, route_agents
from prguard.models import AgentRun, Finding, PRReviewRequest
from prguard.verifier import verify_findings


def test_dynamic_security_route_is_deterministic():
    case = next(item for item in load_dataset()["cases"] if item["case_id"] == "sql-injection-05")
    snapshot, _ = snapshot_for(case)
    assert classify_risk(snapshot).value == "high"
    assert "SecurityAgent" in route_agents(snapshot, classify_risk(snapshot), "dynamic")


def test_verifier_rejects_untrusted_path_and_bad_line():
    case = load_dataset()["cases"][0]
    snapshot, sources = snapshot_for(case)
    findings = [
        Finding(category="bug", severity="high", file_path="other.py", start_line=1, end_line=1,
                title="Bad path finding", description="This finding has no trusted file evidence.",
                evidence="xyz", suggestion="Fix it.", confidence=0.8),
        Finding(category="bug", severity="high", file_path=case["file_path"], start_line=99, end_line=99,
                title="Bad line finding", description="This finding points beyond the file.",
                evidence="xyz", suggestion="Fix it.", confidence=0.8),
    ]
    verified, rejected = verify_findings(findings, snapshot, sources)
    assert verified == []
    assert len(rejected) == 2


def test_metrics_use_zero_for_zero_denominators():
    result = evaluate_cases([{"case_id": "normal", "file_path": "a.py", "line_range": [1, 1], "category": "other", "defect_type": "normal", "description": "No defect", "is_real_defect": False}], {"normal": []})
    assert result["defect_precision"] == 0.0
    assert result["defect_recall"] == 0.0


def test_offline_fixture_runner_returns_structured_run():
    case = load_dataset()["cases"][0]
    _, sources = snapshot_for(case)
    run = asyncio.run(OfflineFixtureRunner(sources)("CodeQualityAgent", "context", None, semaphore=asyncio.Semaphore(1), timeout_seconds=5))
    assert run.status == "success"
    assert run.findings


def test_context_bundle_deduplicates_and_limits_related_files():
    case = load_dataset()["cases"][0]
    snapshot, sources = snapshot_for(case)
    bundle = build_context_bundle(snapshot, sources, {"app/test_profile.py": sources[case["file_path"]] + "\n"})
    assert bundle.optimized_tokens <= bundle.full_tokens
    assert bundle.selected_context_paths == ("app/test_profile.py",)


def test_python_context_follows_import_and_tests():
    related = related_python_paths(
        ["app/service.py"],
        {"app/service.py": "from app.models import User\n"},
        ["app/service.py", "app/models.py", "tests/test_service.py"],
    )
    assert "app/models.py" in related
    assert "tests/test_service.py" in related


def test_python_symbols_extracts_functions_classes_and_calls():
    symbols = python_symbols("app/service.py", "class User:\n    pass\n\ndef load():\n    return User()\n")
    assert symbols["classes"] == ["User"]
    assert "load" in symbols["functions"]
    assert "User" in symbols["calls"]


def test_flow_isolates_agent_runner_exception():
    case = load_dataset()["cases"][0]
    snapshot, sources = snapshot_for(case)

    async def runner(agent, context, model, *, semaphore, timeout_seconds, prompt_version=None):
        del context, model, semaphore, timeout_seconds, prompt_version
        if agent == "TestImpactAgent":
            raise RuntimeError("fixture failure")
        return AgentRun(agent=agent, status="success", started_at="now", ended_at="now", calls=1)

    request = PRReviewRequest(repo=snapshot.repo, pr_number=snapshot.pr_number, strategy="dynamic", mode="offline", output_dir="/private/tmp/prguard-test")
    flow = ReviewFlow(github_client=FixtureClient(snapshot, sources), agent_runner=runner)
    flow.kickoff(inputs={"request": request.model_dump()})
    assert flow.state.review_status == "partial"
    assert any(error["type"] == "RuntimeError" for error in flow.state.errors)
