from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from prguard.context import approximate_tokens
from prguard.models import AgentRun, Finding, FindingType, IssueType, PRFile, PRReviewRequest, PRSnapshot

DATASET = Path(__file__).parent / "datasets" / "prguard_v1.json"
DATASET_V2 = Path(__file__).parent / "datasets" / "prguard_v2.json"


def load_dataset() -> dict:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    if len(data["cases"]) < 15:
        raise ValueError("PRGuard evaluation dataset must contain at least 15 cases")
    return data


def case_files(case: dict) -> list[dict]:
    """Uniform view over single-file (v1) and multi-file (v2) cases."""
    if "files" in case:
        return list(case["files"])
    return [{"path": case["file_path"], "code": case["code"]}]


def case_defects(case: dict) -> list[dict]:
    """All labelled defects in a case, in the shape the matcher expects."""
    if "defects" in case:
        return list(case["defects"])
    return [case] if case.get("is_real_defect") else []


def case_has_defect(case: dict) -> bool:
    if "has_defect" in case:
        return bool(case["has_defect"])
    return bool(case.get("is_real_defect"))


def snapshot_for(case: dict) -> tuple[PRSnapshot, dict[str, str]]:
    if "files" in case:
        return _multi_file_snapshot(case)

    source = case["code"]
    lines = source.splitlines()
    patch = f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join(f"+{line}" for line in lines)
    snapshot = PRSnapshot(
        repo="prguard/fixture",
        pr_number=int(hashlib.sha256(case["case_id"].encode()).hexdigest()[:8], 16) % 900_000 + 1,
        title=f"Fixture: {case['case_id']}",
        body="Local evaluation fixture.",
        base_sha="fixture-base",
        head_sha="fixture-head",
        files=[PRFile(path=case["file_path"], status="modified", patch=patch, changes=len(lines))],
        diff=f"diff -- {case['file_path']} (modified)\n{patch}",
    )
    return snapshot, {case["file_path"]: source}


def _multi_file_snapshot(case: dict) -> tuple[PRSnapshot, dict[str, str]]:
    """Build a snapshot whose diff really contains several files.

    Each file gets its own hunk, so path recovery has a genuine choice between
    candidates instead of the trivially unique single-file case.
    """
    files: list[PRFile] = []
    sources: dict[str, str] = {}
    hunks: list[str] = []
    for entry in case["files"]:
        source = entry["code"]
        lines = source.splitlines()
        status = entry.get("status", "modified")
        sources[entry["path"]] = source
        patch = f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join(f"+{line}" for line in lines)
        files.append(
            PRFile(
                path=entry["path"],
                status=status,
                patch=None if status == "removed" else patch,
                changes=len(lines),
            )
        )
        hunks.append(
            f"diff --git a/{entry['path']} b/{entry['path']}\n"
            f"--- a/{entry['path']}\n"
            f"+++ b/{entry['path']}\n"
            + (patch if status != "removed" else "")
        )
    snapshot = PRSnapshot(
        repo="prguard/fixture",
        pr_number=int(hashlib.sha256(case["case_id"].encode()).hexdigest()[:8], 16) % 900_000 + 1,
        title=case.get("pr_title", f"Fixture: {case['case_id']}"),
        body="Local evaluation fixture.",
        base_sha="fixture-base",
        head_sha="fixture-head",
        files=files,
        diff="\n".join(hunks),
    )
    return snapshot, sources


def load_dataset_v2(path: Path | None = None) -> dict:
    data = json.loads((path or DATASET_V2).read_text(encoding="utf-8"))
    return data


class FixtureClient:
    def __init__(self, snapshot: PRSnapshot, sources: dict[str, str]):
        self.snapshot = snapshot
        self.sources = sources

    def fetch_pr(self, _: PRReviewRequest) -> PRSnapshot:
        return self.snapshot

    def fetch_tree(self, _: str, __: str) -> list[str]:
        return list(self.sources)

    def fetch_file(self, _: str, path: str, __: str) -> str | None:
        return self.sources.get(path)


class OfflineFixtureRunner:
    """Deterministic lightweight rules for offline pipeline and metric smoke tests."""

    def __init__(self, sources: dict[str, str]):
        self.sources = sources

    async def __call__(self, agent: str, context: str, model: str | None, *, semaphore: asyncio.Semaphore, timeout_seconds: int) -> AgentRun:
        del model, timeout_seconds
        started = datetime.now(timezone.utc)
        clock = time.monotonic()
        findings: list[Finding] = []
        for path, source in self.sources.items():
            rules = [
                ("SecurityAgent", r"execute\s*\(\s*f['\"]", "security", "critical", IssueType.SQL_INJECTION, "SQL injection through string interpolation", "User input is interpolated into SQL, allowing query structure to be changed.", "Bind input values as query parameters."),
                ("SecurityAgent", r"subprocess\.\w+\(.*shell\s*=\s*True", "security", "critical", IssueType.UNSAFE_COMMAND, "Shell command injection", "A shell is invoked with untrusted command input.", "Pass a validated argument vector and keep shell execution disabled."),
                ("SecurityAgent", r"chmod\s*\([^\n]*0o777", "security", "high", IssueType.OVERLY_PERMISSIVE_FILE_MODE, "World-writable file permissions", "The file is made readable, writable, and executable by all users.", "Grant only the minimum required permissions."),
                ("SecurityAgent", r"(?:api[_-]?token|api[_-]?key)\s*=\s*['\"](?:sk-|gh[pousr]_|[A-Za-z0-9_-]{24,})", "security", "critical", IssueType.HARDCODED_SECRET, "Hard-coded credential", "A credential-like value is embedded in source.", "Load the credential from a secret store or environment."),
                ("CodeQualityAgent", r"range\s*\(\s*len\([^)]*\)\s*\+\s*1\s*\)", "bug", "high", IssueType.BOUNDARY_CONDITION, "Loop exceeds collection bounds", "The loop includes an index equal to the collection length.", "Use range(len(items)) or iterate directly over the items."),
                ("CodeQualityAgent", r"return\s+user\.\w+", "bug", "high", IssueType.NULL_DEREFERENCE, "Possible null dereference", "The optional user is dereferenced without checking for None.", "Check for None before accessing user attributes."),
                ("CodeQualityAgent", r"json\.loads\s*\(", "bug", "medium", IssueType.MISSING_EXCEPTION_HANDLING, "Malformed JSON is not handled", "Invalid JSON raises an exception that escapes this parsing boundary.", "Handle JSONDecodeError and return a clear validation error."),
                ("CodeQualityAgent", r"(?m)^\s*\w+\s*=\s*open\s*\(", "bug", "medium", IssueType.RESOURCE_LEAK, "File handle may leak", "The opened file is not managed by a context manager.", "Use `with open(...)` to close the handle on every path."),
                ("CodeQualityAgent", r"\breturn\s+[^\n]+\s/\s*\w+", "bug", "medium", IssueType.DIVISION_BY_ZERO, "Possible division by zero", "The divisor can be zero and is not checked.", "Validate the divisor before division."),
                ("TestImpactAgent", r"def\s+transfer_funds\s*\(", "testing", "medium", IssueType.MISSING_TEST_COVERAGE, "Transfer failure paths lack tests", "The transfer mutates two accounts but the fixture provides no tests for debit or credit failure.", "Add tests for invalid amounts and partial failure rollback."),
            ]
            for target_agent, pattern, category, severity, issue_type, title, description, suggestion in rules:
                if target_agent != agent:
                    continue
                match = re.search(pattern, source, flags=re.IGNORECASE)
                if not match:
                    continue
                line = source.count("\n", 0, match.start()) + 1
                evidence = source.splitlines()[line - 1].strip()
                findings.append(Finding(
                    category=category,
                    severity=severity,
                    issue_type=issue_type,
                    finding_type=FindingType.DEFECT,
                    trigger_condition=description,
                    expected_behavior="The risky value is validated or contained before use.",
                    actual_behavior=description,
                    file_path=path,
                    start_line=line,
                    end_line=line,
                    title=title,
                    description=description,
                    evidence=evidence,
                    suggestion=suggestion,
                    confidence=0.78,
                    source_agent=agent,
                ))
        async with semaphore:
            await asyncio.sleep(0)
        ended = datetime.now(timezone.utc)
        return AgentRun(
            agent=agent,
            status="success",
            started_at=started.isoformat(),
            ended_at=ended.isoformat(),
            duration_seconds=time.monotonic() - clock,
            calls=1,
            input_tokens=approximate_tokens(context),
            total_tokens=approximate_tokens(context),
            findings=findings,
        )
