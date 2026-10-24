from __future__ import annotations

import re
from dataclasses import dataclass

from prguard.evidence import CONTRADICTED, SUPPORTED, evaluate_condition
from prguard.paths import PathResolver, normalize_path
from prguard.models import (
    EvidenceLevel,
    Finding,
    FindingType,
    PRSnapshot,
    VerificationStatus,
)


def _normalized_text(finding: Finding) -> str:
    return re.sub(r"\W+", " ", f"{finding.title} {finding.description}".lower()).strip()


def _norm_evidence(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _text_overlap(left: str, right: str) -> float:
    a, b = set(left.split()), set(right.split())
    return len(a & b) / max(1, len(a | b))


def _trigger_conflicts(left: Finding, right: Finding) -> bool:
    """Two stated, disjoint triggers describe different failure conditions."""
    a, b = _norm_evidence(left.trigger_condition), _norm_evidence(right.trigger_condition)
    if not a or not b:
        return False
    return not (set(a.split()) & set(b.split()))


def _same_issue(left: Finding, right: Finding) -> bool:
    """Decide whether two accepted findings report one problem.

    Signals, strongest first. Deliberately not text-similarity driven: the agents
    answer in Chinese with different wording for the same defect, which is exactly the
    case that must merge, while two different defects on one line must not.

    1. same file (after path normalization)
    2. same location: line numbers within two, or both absent
    3. same issue kind: two different specific issue_type values are different defects
    4. evidence identity: identical or contained evidence is the same citation
    5. trigger condition: explicitly disjoint triggers are different defects
    6. only then fall back to wording similarity
    """
    if normalize_path(left.file_path) != normalize_path(right.file_path):
        return False

    a_line, b_line = left.start_line or 0, right.start_line or 0
    if a_line and b_line and abs(a_line - b_line) > 2:
        return False

    left_type, right_type = left.issue_type.value, right.issue_type.value
    if left_type != right_type and "other" not in (left_type, right_type):
        return False

    evidence_left, evidence_right = _norm_evidence(left.evidence), _norm_evidence(right.evidence)
    if evidence_left and evidence_right:
        if evidence_left == evidence_right:
            # A byte-identical code citation is the same evidence, whatever the prose.
            # Trigger wording differs freely between agents (and Chinese has no word
            # boundaries to compare), so it must not override an exact citation.
            return True
        if evidence_left in evidence_right or evidence_right in evidence_left:
            return True
        same_place = bool(a_line) and a_line == b_line
        # Here the evidence differs, so a stated, disjoint trigger is decisive.
        if same_place and left_type == right_type != "other" and _trigger_conflicts(left, right) is False:
            if _text_overlap(evidence_left, evidence_right) >= 0.6:
                return True

    if _trigger_conflicts(left, right):
        return False

    overlap = _text_overlap(_normalized_text(left), _normalized_text(right))
    return overlap >= 0.42 or (bool(a_line) and a_line == b_line and overlap >= 0.25)


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
    """Test files in the supplied context that correspond to `module_path`.

    The match must be about this module. A name that merely starts with ``test_``
    says nothing about which module it exercises, so it is not accepted: doing so
    let one unrelated test file refute every test-gap claim in the repository.
    """
    if not candidates:
        return set()
    parts = module_path.split("/")
    stem = parts[-1].removesuffix(".py")
    package = parts[-2] if len(parts) > 1 else ""
    if not stem:
        return set()

    accepted = {
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"tests_{stem}.py",
    }
    found = set()
    for path in candidates:
        if not path.endswith(".py") or not TEST_FILE.search(path):
            continue
        name = path.rsplit("/", 1)[-1]
        if name in accepted:
            found.add(path)
            continue
        # tests/test_users.py covering app/models/users.py: match the package too.
        if package and name in {f"test_{package}.py", f"{package}_test.py"}:
            found.add(path)
    return found


def _test_sources_for(module_path: str, source_map: dict[str, str]) -> dict[str, str]:
    """Retrieved test files for this module together with their contents."""
    return {
        path: source_map.get(path, "")
        for path in _test_paths_for(module_path, set(source_map))
    }


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
    resolve_paths: bool = False,
    dedupe: bool = True,
) -> tuple[list[Finding], list[dict[str, str]]]:
    """Layer 1 deterministic checks, layer 2 condition checks, then deduplicate."""
    valid_paths = {file.path for file in snapshot.files} | (related_paths or set())
    touched = changed_lines(snapshot)
    resolver = (
        PathResolver(
            [file.path for file in snapshot.files],
            source_map,
            {file.previous_filename: file.path for file in snapshot.files if file.previous_filename},
        )
        if resolve_paths
        else None
    )
    accepted: list[Finding] = []
    rejected: list[dict[str, str]] = []

    for original in findings:
        # Verify a copy: callers keep the agent output for pre-verifier scoring and
        # for replay, so it must stay byte-identical after verification.
        finding = original.model_copy(deep=True)

        if resolver is not None:
            resolution = resolver.resolve(finding.file_path, finding.start_line, finding.evidence)
            finding.reported_path = resolution.original
            finding.path_status = resolution.status
            finding.path_basis = resolution.basis
            if resolution.is_resolved:
                finding.file_path = resolution.resolved or finding.file_path
            else:
                rejected.append(
                    _Rejection(
                        "path_unresolved", f"{resolution.status}: {resolution.basis}"
                    ).as_dict(finding)
                )
                continue

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
                test_sources=_test_sources_for(finding.file_path, source_map),
                # The finding's own wording decides whether a retrieved failure-path
                # test covers the claimed failure, not just that some test raises.
                claim_text=f"{finding.title} {finding.description} {finding.trigger_condition}",
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

    if not dedupe:
        return accepted, rejected

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
