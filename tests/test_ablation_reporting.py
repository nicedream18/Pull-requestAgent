from __future__ import annotations

from evals.ablation import _failure_summary, _raw_path_validity
from evals.fixtures import load_dataset
from prguard.agents import FindingParseError, _agent_findings
from prguard.models import AgentRun, FlowState


def test_malformed_agent_output_is_countable_and_does_not_echo_response():
    raw = "not-json; private source omitted"
    try:
        _agent_findings(raw, "CodeQualityAgent")
    except FindingParseError as exc:
        assert "private source" not in str(exc)
    else:
        raise AssertionError("malformed output must not be silently treated as an empty review")


def test_failure_summary_separates_timeout_parse_and_api_failures():
    cases = [{"case_id": "one"}, {"case_id": "two"}, {"case_id": "three"}]
    states = [
        FlowState(agent_runs=[
            AgentRun(agent="CodeQualityAgent", status="failed", error_type="FindingParseError", started_at="", ended_at=""),
            AgentRun(agent="SecurityAgent", status="timeout", error_type="TimeoutError", started_at="", ended_at=""),
        ]),
        FlowState(agent_runs=[
            AgentRun(agent="SecurityAgent", status="failed", error_type="RateLimitError", started_at="", ended_at=""),
            AgentRun(agent="TestImpactAgent", status="success", started_at="", ended_at=""),
        ]),
        FlowState(),
    ]
    summary = _failure_summary(cases, states, [{"case_id": "three", "error_type": "RuntimeError"}])
    assert summary["failed_case_count"] == 3
    assert summary["failed_agent_run_count"] == 3
    assert summary["agent_run_count"] == 4
    assert summary["agent_failure_rate"] == 0.75
    assert summary["timeout_runs"] == 1
    assert summary["parse_error_runs"] == 1
    assert summary["api_error_runs"] == 1


def test_raw_path_rate_is_zero_when_no_model_findings_exist():
    case = load_dataset()["cases"][0]
    stats = _raw_path_validity([case], [FlowState()])
    assert stats["raw_findings"] == 0
    assert stats["valid_reported_paths"] == 0
    assert stats["rate"] == 0.0

