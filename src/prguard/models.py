from __future__ import annotations

from enum import StrEnum
from typing import Literal, Optional
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    LARGE = "large"


class IssueType(StrEnum):
    """Controlled defect vocabulary shared by agents, verifier and evaluation."""

    NULL_DEREFERENCE = "null_dereference"
    MISSING_EXCEPTION_HANDLING = "missing_exception_handling"
    BOUNDARY_CONDITION = "boundary_condition"
    RESOURCE_LEAK = "resource_leak"
    SQL_INJECTION = "sql_injection"
    UNSAFE_COMMAND = "unsafe_command"
    MISSING_TEST_COVERAGE = "missing_test_coverage"
    OVERLY_PERMISSIVE_FILE_MODE = "overly_permissive_file_mode"
    DIVISION_BY_ZERO = "division_by_zero"
    HARDCODED_SECRET = "hardcoded_secret"
    CROSS_FILE_BREAKAGE = "cross_file_breakage"
    OTHER = "other"


class FindingType(StrEnum):
    """A defect claims a provable wrong behavior; an advisory is an improvement."""

    DEFECT = "defect"
    ADVISORY = "advisory"


class EvidenceLevel(StrEnum):
    EXACT = "exact"
    PARTIAL = "partial"
    INFERRED = "inferred"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    UNCERTAIN = "uncertain"
    REJECTED = "rejected"


class PRReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str | None = Field(default=None, description="GitHub repository as owner/name")
    pr_number: int | None = Field(default=None, gt=0)
    pr_url: str | None = None
    strategy: Literal["single", "static", "dynamic"] = "dynamic"
    mode: Literal["online", "offline"] = "online"
    model: str | None = None
    output_dir: str = "artifacts"
    concurrency: int = Field(default=3, ge=1, le=8)
    timeout_seconds: int = Field(default=90, ge=5, le=600)
    max_agent_calls: int = Field(default=6, ge=1, le=12)
    # Ablation switch: disable layer-2 condition checks to measure their effect.
    condition_checks: bool = True

    @model_validator(mode="after")
    def validate_target(self) -> "PRReviewRequest":
        if self.pr_url:
            parsed = urlsplit(self.pr_url)
            parts = parsed.path.strip("/").split("/")
            if (
                parsed.scheme != "https"
                or parsed.hostname != "github.com"
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
                or parsed.query
                or parsed.fragment
                or len(parts) != 4
                or parts[2] != "pull"
                or not parts[3].isdigit()
            ):
                raise ValueError("pr_url must be a canonical https://github.com/owner/repo/pull/123 URL")
            url_repo = f"{parts[0]}/{parts[1]}"
            if self.repo and self.repo.casefold() != url_repo.casefold():
                raise ValueError("repo does not match pr_url")
            if self.pr_number and self.pr_number != int(parts[3]):
                raise ValueError("pr_number does not match pr_url")
            self.repo = url_repo
            self.pr_number = int(parts[3])

        if not self.repo or not self.pr_number:
            raise ValueError("provide pr_url or both repo and pr_number")
        pieces = self.repo.split("/")
        if len(pieces) != 2 or any(not p or p in {".", ".."} for p in pieces):
            raise ValueError("repo must use owner/name format")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
        if any(any(char not in allowed for char in piece) for piece in pieces):
            raise ValueError("repo contains invalid characters")
        return self


class PRFile(BaseModel):
    path: str
    status: str
    patch: str | None = None
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    previous_filename: str | None = None

    @model_validator(mode="after")
    def validate_path(self) -> "PRFile":
        parts = self.path.replace("\\", "/").split("/")
        if self.path.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("GitHub file path must be a repository-relative path")
        return self


class PRSnapshot(BaseModel):
    repo: str
    pr_number: int
    title: str
    body: str = ""
    base_sha: str
    head_sha: str
    files: list[PRFile]
    diff: str
    diff_complete: bool = True
    diff_omitted_files: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["bug", "security", "testing", "quality", "performance", "other"]
    severity: Severity
    file_path: str
    # CrewAI 0.98.0's schema parser handles typing.Optional but not PEP 604
    # unions when generating structured-output instructions.
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)
    title: str = Field(min_length=4, max_length=180)
    description: str = Field(min_length=12, max_length=3000)
    evidence: str = Field(min_length=3, max_length=1200)
    suggestion: str = Field(min_length=3, max_length=1200)
    confidence: float = Field(ge=0, le=1)
    source_agent: str = ""
    needs_human_review: bool = False

    # Classification. `issue_type` is the deterministic channel used by the
    # verifier and the evaluator, so it must stay a closed vocabulary.
    issue_type: IssueType = IssueType.OTHER
    finding_type: FindingType = FindingType.DEFECT
    # A defect is only actionable when its trigger is stated; advisories may leave it blank.
    trigger_condition: str = Field(default="", max_length=600)
    expected_behavior: str = Field(default="", max_length=600)
    actual_behavior: str = Field(default="", max_length=600)

    # Populated by the verifier, not by the agent.
    evidence_level: EvidenceLevel = EvidenceLevel.PARTIAL
    verification_status: VerificationStatus = VerificationStatus.UNCERTAIN
    rejection_reason: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_lines(self) -> "Finding":
        if self.start_line and self.end_line and self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.confidence < 0.55:
            self.needs_human_review = True
        return self

    @model_validator(mode="after")
    def validate_defect_claims(self) -> "Finding":
        # A defect without a location or trigger cannot be verified, so it is
        # demoted rather than silently accepted as a confirmed defect.
        if self.finding_type is FindingType.DEFECT:
            if not self.trigger_condition.strip():
                self.finding_type = FindingType.ADVISORY
                self.needs_human_review = True
            elif self.start_line is None:
                self.needs_human_review = True
        return self


class AgentFindings(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class AgentRun(BaseModel):
    agent: str
    status: Literal["success", "failed", "timeout", "skipped"]
    started_at: str
    ended_at: str
    duration_seconds: float = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error_type: str | None = None
    error: str | None = None
    findings: list[Finding] = Field(default_factory=list)


class FlowState(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str = ""
    request: dict = Field(default_factory=dict)
    repo_name: str = ""
    pr_number: int = 0
    head_sha: str = ""
    changed_files: list[str] = Field(default_factory=list)
    diff: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    selected_agents: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    verified_findings: list[Finding] = Field(default_factory=list)
    token_usage: dict[str, int] = Field(default_factory=lambda: {"input": 0, "output": 0, "total": 0, "calls": 0})
    execution_time: float = 0
    errors: list[dict[str, str]] = Field(default_factory=list)
    review_status: Literal["queued", "running", "completed", "partial", "failed"] = "queued"
    agent_runs: list[AgentRun] = Field(default_factory=list)
    metrics: dict[str, float | int | str | None] = Field(default_factory=dict)
    trace_events: list[dict[str, str | int | float]] = Field(default_factory=list)
    historical_prs: list[dict[str, str | int]] = Field(default_factory=list)
