from __future__ import annotations

import re
from dataclasses import dataclass

from prguard.evidence import CONTRADICTED, SUPPORTED, evaluate_condition
from prguard.models import (
    EvidenceLevel,
    Finding,
    FindingType,
    PRSnapshot,
    VerificationStatus,
)


def _normalized_text(finding: Finding) -> str:
    return re.sub(r"\W+", " ", f"{finding.title} {finding.description}".lower()).strip()


def _same_issue(left: Finding, right: Finding) -> bool:
    if left.file_path != right.file_path:
        return False
    if left.issue_type != right.issue_type and left.category != right.category:
        return False
    left_line, right_line = left.start_line or 0, right.start_line or 0
    if left_line and right_line and abs(left_line - right_line) > 5:
        return False
    a, b = set(_normalized_text(left).split()), set(_normalized_text(right).split())
    overlap = len(a & b) / max(1, len(a | b))
    if left.issue_type != right.issue_type and left.issue_type.value != "other":
        # Distinct issue types on the same file are distinct problems.
        return False
    return overlap >= 0.42 or (left_line and left_line == right_line and overlap >= 0.25)


def changed_lines(snapshot: PRSnapshot) -> dict[str, set[int]]:
    """Line numbers added or replaced by the diff, per file."""
    mapping: dict[str, set[int]] = {}
    current: str | None = None
    new_line = 0
    for raw in snapshot.diff.splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split()
            if len(parts) >= 4:
                current = parts[3].removeprefix("b/")
                mapping.setdefault(current, set())
            continue
        if raw.startswith("diff -- ") and not raw.startswith("diff --git"):
            rest = raw[len("diff -- "):]
            candidate = rest.split(" (")[0].strip().removeprefix("a/").removeprefix("b/")
            if candidate:
                current = candidate
                mapping.setdefault(current, set())
            continue
        if raw.startswith("+++ b/"):
            current = raw[6:].strip()
            mapping.setdefault(current, set())
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            if match:
                new_line = int(match.group(1))
            continue
        if current is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            mapping[current].add(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            continue
        else:
            new_line += 1
    return mapping


TEST_FILE = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]+\.py$|_test\.py$")


def _test_paths_for(module_path: str, candidates: set[str] | None) -> set[str]:
    """Test files in the supplied context that plausibly cover `module_path`."""
    if not candidates:
        return set()
    stem = module_path.rsplit("/", 1)[-1].removesuffix(".py")
    found = set()
    for path in candidates:
        name = path.rsplit("/", 1)[-1]
        if not TEST_FILE.search(path) or not path.endswith(".py"):
            continue
        if stem and (stem in name or name.startswith("test_")):
            found.add(path)
    return found


@dataclass
class _Rejection:
    kind: str
    message: str

    def as_dict(self, finding: Finding) -> dict[str, str]:
        return {
            "type": self.kind,
            "message": self.message,
            "file_path": finding.file_path,
            "issue_type": finding.issue_type.value,
            "source_agent": finding.source_agent,
        }


def _layer_one(
    finding: Finding, snapshot: PRSnapshot, source_map: dict[str, str], valid_paths: set[str]
) -> _Rejection | None:
    """Schema, path, source version, line range and evidence-text checks."""
    if finding.file_path not in valid_paths:
        return _Rejection("invalid_path", f"Rejected finding for untrusted path {finding.file_path}")

    text = source_map.get(finding.file_path)
    if text is not None:
        lines = text.splitlines()
        if finding.start_line and finding.start_line > len(lines):
            return _Rejection("invalid_line", f"Rejected out-of-range line in {finding.file_path}")
        if finding.end_line and finding.end_line > len(lines):
            return _Rejection("invalid_line", f"Rejected out-of-range end line in {finding.file_path}")
        if finding.start_line:
            last = (finding.end_line or finding.start_line) + 2
            window = "\n".join(lines[max(0, finding.start_line - 2):min(len(lines), last)])
            if finding.evidence.strip() not in window:
                return _Rejection(
                    "evidence_mismatch", f"Evidence did not match the cited lines in {finding.file_path}"
                )
    elif finding.evidence.strip() not in snapshot.diff:
        return _Rejection(
            "evidence_mismatch", f"Evidence was not present in the returned Diff for {finding.file_path}"
        )
    return None


def _downgrade(finding: Finding, reason: str) -> Finding:
    """Keep the report visible but stop calling it a confirmed defect."""
    return finding.model_copy(
        update={
            "finding_type": FindingType.ADVISORY,
            "verification_status": VerificationStatus.UNCERTAIN,
            "evidence_level": EvidenceLevel.INFERRED,
            "needs_human_review": True,
            "rejection_reason": reason,
        }
    )


def verify_findings(
    findings: list[Finding],
    snapshot: PRSnapshot,
    source_map: dict[str, str],
    related_paths: set[str] | None = None,
    *,
    check_conditions: bool = True,
    require_trigger: bool = True,
) -> tuple[list[Finding], list[dict[str, str]]]:
    """Layer 1 deterministic checks, layer 2 condition checks, then deduplicate."""
    valid_paths = {file.path for file in snapshot.files} | (related_paths or set())
    # A retrieved file is evidence even when it is not part of the changed set, and
    # the test-coverage check needs to see those paths.
    known_paths = valid_paths | set(source_map)
    touched = changed_lines(snapshot)
    accepted: list[Finding] = []
    rejected: list[dict[str, str]] = []

    for finding in findings:
        failure = _layer_one(finding, snapshot, source_map, valid_paths)
        if failure is not None:
            rejected.append(failure.as_dict(finding))
            continue

        if (
            require_trigger
            and finding.finding_type is FindingType.DEFECT
            and not finding.trigger_condition.strip()
        ):
            # An agent that cannot state the trigger has not proven a defect.
            finding = _downgrade(finding, "no trigger condition was stated for this claim")
            finding.confidence = min(finding.confidence, 0.54)

        finding.evidence_level = EvidenceLevel.EXACT
        if (
            finding.start_line
            and touched.get(finding.file_path)
            and finding.start_line not in touched[finding.file_path]
        ):
            # Reported on unchanged context: real, but not introduced by this PR.
            finding.evidence_level = EvidenceLevel.PARTIAL

        if check_conditions and finding.issue_type.value != "other":
            verdict = evaluate_condition(
                finding.issue_type,
                source_map.get(finding.file_path, ""),
                finding.start_line,
                test_paths_for_module=_test_paths_for(finding.file_path, known_paths),
            )
            if verdict.status == CONTRADICTED:
                rejected.append(
                    _Rejection(
                        "condition_not_met", f"{finding.issue_type.value}: {verdict.reason}"
                    ).as_dict(finding)
                )
                continue
            if verdict.status == SUPPORTED:
                finding.verification_status = VerificationStatus.VERIFIED
                if not finding.trigger_condition.strip():
                    finding.trigger_condition = verdict.reason
            elif finding.finding_type is FindingType.DEFECT:
                finding = _downgrade(finding, verdict.reason)

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
        decided = [item for item in group if item.finding_type is FindingType.DEFECT] or group
        chosen = max(
            decided,
            key=lambda item: (
                item.verification_status == VerificationStatus.VERIFIED,
                item.confidence,
                item.severity.value,
            ),
        )
        agents = sorted({item.source_agent for item in group if item.source_agent})
        if len(agents) > 1:
            chosen = chosen.model_copy(update={"source_agent": ", ".join(agents)})
        merged.append(chosen)
    return merged, rejected
