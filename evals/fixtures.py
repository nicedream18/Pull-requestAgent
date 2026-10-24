from __future__ import annotations

import asyncio
import difflib
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


CONTEXT_LINES = 3


def _new_file_hunk(source: str) -> tuple[str, int]:
    """All-additions hunk for a file that does not exist in the base revision."""
    lines = source.splitlines()
    return f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join(f"+{line}" for line in lines), len(lines)


def _modified_file_hunk(base: str, head: str) -> tuple[str, int, int, int]:
    """Real unified hunk with context, additions and deletions.

    Returns ``(hunk, additions, deletions, first_changed_head_line)``. Line numbers in
    the ``+`` side are the head revision's, which is what a finding must cite.
    """
    base_lines = base.splitlines()
    head_lines = head.splitlines()
    diff_lines = list(
        difflib.unified_diff(base_lines, head_lines, fromfile="a", tofile="b", n=CONTEXT_LINES, lineterm="")
    )
    hunk: list[str] = []
    additions = deletions = 0
    for line in diff_lines[2:]:  # drop the ---/+++ banners; the caller writes them
        hunk.append(line)
        if line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    first_changed = None
    head_line = 0
    for line in hunk:
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                head_line = int(match.group(1))
        elif line.startswith("+"):
            if first_changed is None:
                first_changed = head_line
            head_line += 1
        elif line.startswith("-"):
            continue
        else:
            head_line += 1
    return "\n".join(hunk), additions, deletions, first_changed or 1


def _multi_file_snapshot(case: dict) -> tuple[PRSnapshot, dict[str, str]]:
    """Build a snapshot whose diff really contains the changed files.

    Two modes, selected per file by whether the entry carries `base_code`:

    * **real diff** (`base_code` present): a proper unified hunk with context lines,
      deletions and head-side line numbers, plus `previous_filename` for renames.
    * **synthetic new file** (no `base_code`): an all-additions hunk. This is honest
      for a genuinely added file but it means every line counts as changed, so a
      finding on unchanged context cannot be distinguished. The older 32 cases use
      this mode; `docs/evaluation-protocol.md` records the limitation.
    """
    files: list[PRFile] = []
    sources: dict[str, str] = {}
    hunks: list[str] = []
    for entry in case["files"]:
        source = entry["code"]
        status = entry.get("status", "modified")
        sources[entry["path"]] = source
        previous = entry.get("previous_filename")
        base_code = entry.get("base_code")

        if status == "removed":
            base_lines = (base_code if base_code is not None else source).splitlines()
            patch = f"@@ -1,{len(base_lines)} +0,0 @@\n" + "\n".join(f"-{line}" for line in base_lines)
            additions, deletions, changes = 0, len(base_lines), len(base_lines)
        elif base_code is not None:
            patch, additions, deletions, _ = _modified_file_hunk(base_code, source)
            changes = additions + deletions
        else:
            patch, additions = _new_file_hunk(source)
            deletions, changes = 0, additions
            if status == "modified":
                # No base revision was supplied, so the diff can only be all-additions.
                # Record the file as added to keep the snapshot self-consistent.
                status = "added"

        files.append(
            PRFile(
                path=entry["path"],
                status=status,
                patch=None if status == "removed" else patch,
                additions=additions,
                deletions=deletions,
                changes=changes,
                previous_filename=previous,
            )
        )
        banner = f"diff --git a/{previous or entry['path']} b/{entry['path']}\n"
        if status == "added":
            banner += "new file mode 100644\n"
        elif status == "removed":
            banner += "deleted file mode 100644\n"
        elif previous:
            banner += f"rename from {previous}\nrename to {entry['path']}\n"
        hunks.append(
            banner
            + f"--- a/{previous or entry['path']}\n"
            + f"+++ b/{entry['path']}\n"
            + patch
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

    async def __call__(
        self,
        agent: str,
        context: str,
        model: str | None,
        *,
        semaphore: asyncio.Semaphore,
        timeout_seconds: int,
        prompt_version: str | None = None,
    ) -> AgentRun:
        del model, timeout_seconds, prompt_version
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
