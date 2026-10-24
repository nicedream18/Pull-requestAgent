import pytest
from pydantic import ValidationError

from prguard.models import Finding, FlowState, PRFile, PRReviewRequest


def test_pr_request_accepts_canonical_url():
    request = PRReviewRequest(pr_url="https://github.com/acme/widgets/pull/42")
    assert request.repo == "acme/widgets"
    assert request.pr_number == 42


@pytest.mark.parametrize("url", [
    "http://github.com/acme/widgets/pull/42",
    "https://evil.example/acme/widgets/pull/42",
    "https://github.com/acme/widgets/pull/42?download=1",
    "https://github.com/acme/widgets/pull/42.patch",
])
def test_pr_request_rejects_noncanonical_urls(url):
    with pytest.raises(ValidationError):
        PRReviewRequest(pr_url=url)


def test_finding_marks_low_confidence_for_human_review():
    finding = Finding(
        category="bug", severity="low", file_path="app.py", start_line=1, end_line=1,
        title="Possible issue", description="This needs more evidence before action.",
        evidence="return value", suggestion="Add a guard.", confidence=0.2,
    )
    assert finding.needs_human_review is True


def test_paths_cannot_escape_repository():
    with pytest.raises(ValidationError):
        PRFile(path="../secret.txt", status="modified")


def test_flow_state_has_id_for_crewai_flow_logging():
    assert FlowState().id
