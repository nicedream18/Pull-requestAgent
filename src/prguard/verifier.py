from __future__ import annotations

import re
from collections import defaultdict

from prguard.models import Finding, PRSnapshot


def _normalized_text(finding: Finding) -> str:
    return re.sub(r"\W+", " ", f"{finding.title} {finding.description}".lower()).strip()


def _same_issue(left: Finding, right: Finding) -> bool:
    if left.file_path != right.file_path or left.category != right.category:
        return False
    left_line, right_line = left.start_line or 0, right.start_line or 0
    if left_line and right_line and abs(left_line - right_line) > 5:
        return False
    a, b = set(_normalized_text(left).split()), set(_normalized_text(right).split())
    overlap = len(a & b) / max(1, len(a | b))
    return overlap >= 0.42 or (left_line and left_line == right_line and overlap >= 0.25)


def verify_findings(
    findings: list[Finding],
    snapshot: PRSnapshot,
    source_map: dict[str, str],
    related_paths: set[str] | None = None,
) -> tuple[list[Finding], list[dict[str, str]]]:
    """Validate schema, path, line, and quoted evidence, then deduplicate reports."""
    valid_paths = {file.path for file in snapshot.files} | (related_paths or set())
    accepted: list[Finding] = []
    rejected: list[dict[str, str]] = []
    for finding in findings:
        if finding.file_path not in valid_paths:
            rejected.append({"type": "invalid_path", "message": f"Rejected finding for untrusted path {finding.file_path}"})
            continue
        text = source_map.get(finding.file_path)
        if text is not None:
            lines = text.splitlines()
            if finding.start_line and finding.start_line > len(lines):
                rejected.append({"type": "invalid_line", "message": f"Rejected out-of-range line in {finding.file_path}"})
                continue
            if finding.end_line and finding.end_line > len(lines):
                rejected.append({"type": "invalid_line", "message": f"Rejected out-of-range end line in {finding.file_path}"})
                continue
            if finding.start_line and finding.evidence.strip() not in "\n".join(lines[max(0, finding.start_line - 2):min(len(lines), finding.end_line or finding.start_line + 2)]):
                rejected.append({"type": "evidence_mismatch", "message": f"Evidence did not match the cited lines in {finding.file_path}"})
                continue
        elif finding.evidence.strip() not in snapshot.diff:
            rejected.append({"type": "evidence_mismatch", "message": f"Evidence was not present in the returned Diff for {finding.file_path}"})
            continue
        if finding.confidence < 0.55:
            finding.needs_human_review = True
        accepted.append(finding)

    groups: list[list[Finding]] = []
    for finding in accepted:
        for group in groups:
            if _same_issue(group[0], finding):
                group.append(finding)
                break
        else:
            groups.append([finding])

    merged: list[Finding] = []
    for group in groups:
        chosen = max(group, key=lambda item: (item.confidence, item.severity.value))
        if len({item.source_agent for item in group}) > 1:
            chosen = chosen.model_copy(update={"source_agent": ", ".join(sorted({item.source_agent for item in group}))})
        merged.append(chosen)
    return merged, rejected
