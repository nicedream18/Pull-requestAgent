"""Safe recovery of a finding's file path.

The first real LLM run lost 21 of 28 findings because the model wrote ``unknown``,
an empty string, or a bare basename instead of the repository path. Rejecting those
outright is correct (an unverifiable location cannot be trusted) but wasteful when the
finding itself carries enough evidence to name the file.

This module recovers a path **only** from data the pipeline already trusts:
the PR's changed files, their sources, the finding's own line number and its verbatim
evidence. It never consults ground truth, never guesses, and never prefers a candidate
because it happens to be the only one in the diff.

Every decision is recorded as a :class:`PathResolution` carrying the original value,
the resolved value, the basis, and a status, so a report can always show what happened
instead of silently rewriting the model's output.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

EXACT = "exact"
NORMALIZED = "normalized"
EVIDENCE_RESOLVED = "evidence_resolved"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"

AUTO_RESOLVED = {EXACT, NORMALIZED, EVIDENCE_RESOLVED}

# A line number must agree with where the evidence actually sits. Agents routinely cite
# the enclosing statement, so a small window is allowed; anything wider is a different
# location and must not be used to pin a file.
LINE_TOLERANCE = 2

# Evidence shorter than this cannot identify a file: it is likely a fragment such as
# `x = 1` that appears in many places.
MIN_EVIDENCE_CHARS = 8

# Values that carry no path information at all.
PLACEHOLDERS = {
    "",
    "unknown",
    "n/a",
    "na",
    "none",
    "null",
    "-",
    "tbd",
    "?",
    "<unknown>",
}

PLACEHOLDER_PREFIX = re.compile(r"^(unknown|n/?a|none|null|tbd)\b", re.IGNORECASE)
WINDOWS_SEPARATOR = re.compile(r"\\")
DUPLICATE_SLASH = re.compile(r"/+")


@dataclass(frozen=True)
class PathResolution:
    """Outcome of resolving one finding's path."""

    status: str
    original: str
    resolved: str | None
    basis: str
    candidates: tuple[str, ...] = ()

    @property
    def is_resolved(self) -> bool:
        return self.status in AUTO_RESOLVED and self.resolved is not None

    @property
    def needs_human_review(self) -> bool:
        return self.status in {AMBIGUOUS, UNRESOLVED}

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "original": self.original,
            "resolved": self.resolved,
            "basis": self.basis,
            "candidates": list(self.candidates),
        }


def normalize_path(raw: str) -> str:
    """Canonical form used for comparison only; never written back silently."""
    path = WINDOWS_SEPARATOR.sub("/", (raw or "").strip().strip("`'\""))
    path = DUPLICATE_SLASH.sub("/", path)
    while path.startswith("./"):
        path = path[2:]
    if path.startswith(("a/", "b/")):
        path = path[2:]
    path = path.rstrip("/")
    if path and not path.startswith("/") and ".." in path.split("/"):
        path = posixpath.normpath(path)
    return path


def is_placeholder(raw: str) -> bool:
    value = (raw or "").strip().strip("`'\"").lower()
    if value in PLACEHOLDERS:
        return True
    return bool(PLACEHOLDER_PREFIX.match(value))


def _normalized_evidence(evidence: str) -> str:
    return re.sub(r"\s+", " ", (evidence or "").strip()).lower()


def _evidence_line(source: str, evidence: str) -> int | None:
    """The single line where the evidence sits, or None when it is absent or repeated."""
    first = (evidence or "").strip().splitlines()
    if not first:
        return None
    needle = first[0].strip()
    if not needle:
        return None
    hits = [index + 1 for index, line in enumerate(source.splitlines()) if needle in line]
    return hits[0] if len(hits) == 1 else None


class PathResolver:
    """Resolve a reported path against the PR's trusted file set."""

    def __init__(
        self,
        changed_files: list[str],
        sources: dict[str, str],
        renames: dict[str, str] | None = None,
    ):
        # Only PR files are trusted. Retrieved context files are deliberately excluded:
        # a finding about unchanged code is still a finding about this PR, but a path
        # guessed from a neighbouring file is not evidence.
        self.candidates: tuple[str, ...] = tuple(changed_files)
        self.sources = {path: sources.get(path, "") for path in self.candidates}
        # A rename means the model may legitimately cite the old name. Map it forward
        # rather than treating it as a wrong path.
        self.renames = {normalize_path(old): new for old, new in (renames or {}).items()}
        self.by_basename: dict[str, list[str]] = {}
        for path in self.candidates:
            self.by_basename.setdefault(posixpath.basename(path), []).append(path)

    def resolve(self, raw_path: str, start_line: int | None, evidence: str) -> PathResolution:
        original = raw_path or ""
        normalized = normalize_path(original)

        if normalized in self.candidates:
            status = EXACT if original == normalized else NORMALIZED
            basis = "path matches a changed file" if status == EXACT else f"normalized to {normalized}"
            return PathResolution(status, original, normalized, basis)

        renamed = self.renames.get(normalized)
        if renamed in self.candidates:
            return PathResolution(
                NORMALIZED, original, renamed, f"{normalized} was renamed to {renamed} in this PR"
            )

        # A specific but non-existent path is not repaired by name similarity. It may
        # still be recoverable from evidence, which is checked below and labelled.
        if normalized and not is_placeholder(original):
            siblings = self.by_basename.get(posixpath.basename(normalized), [])
            if len(siblings) == 1 and normalized.count("/") == 0:
                return PathResolution(
                    NORMALIZED, original, siblings[0], f"unique basename match for {normalized}", tuple(siblings)
                )
            if len(siblings) > 1:
                return PathResolution(
                    AMBIGUOUS,
                    original,
                    None,
                    f"basename {posixpath.basename(normalized)} exists in {len(siblings)} changed files",
                    tuple(siblings),
                )
            return self._from_evidence(original, normalized, start_line, evidence, why="reported path is not a changed file")

        if not normalized:
            return self._from_evidence(original, normalized, start_line, evidence, why="no usable path was reported")

        # Placeholder such as "unknown" or "unknown (see notes)": only evidence may
        # re-establish a location.
        return self._from_evidence(original, normalized, start_line, evidence, why="path is a placeholder")

    def _from_evidence(
        self,
        original: str,
        normalized: str,
        start_line: int | None,
        evidence: str,
        *,
        why: str,
    ) -> PathResolution:
        cleaned = (evidence or "").strip()
        if len(cleaned) < MIN_EVIDENCE_CHARS:
            return PathResolution(
                UNRESOLVED, original, None, f"{why}; evidence is too short to identify a file"
            )
        matches: list[tuple[str, int | None]] = []
        for path in self.candidates:
            source = self.sources.get(path, "")
            if not source:
                continue
            line = _evidence_line(source, cleaned)
            if line is None:
                continue
            matches.append((path, line))

        if not matches:
            return PathResolution(
                UNRESOLVED, original, None, f"{why}; evidence does not appear in any changed file"
            )
        if len(matches) > 1:
            return PathResolution(
                AMBIGUOUS,
                original,
                None,
                f"{why}; evidence appears in {len(matches)} changed files",
                tuple(path for path, _ in matches),
            )

        path, evidence_line = matches[0]
        if start_line is None:
            return PathResolution(
                UNRESOLVED,
                original,
                None,
                f"{why}; evidence is unique to {path} but no line was reported",
                (path,),
            )
        if evidence_line is not None and abs(start_line - evidence_line) > LINE_TOLERANCE:
            return PathResolution(
                UNRESOLVED,
                original,
                None,
                f"{why}; evidence sits at line {evidence_line} but the finding cites line {start_line}",
                (path,),
            )
        return PathResolution(
            EVIDENCE_RESOLVED,
            original,
            path,
            f"{why}; evidence appears only in {path}"
            + (f" at line {evidence_line}" if evidence_line else ""),
            (path,),
        )


def resolver_for(changed_files: list[str], sources: dict[str, str]) -> PathResolver:
    return PathResolver(changed_files, sources)


def summarize(resolutions: list[PathResolution]) -> dict[str, int]:
    """Counts per status, for the ablation report."""
    counts = {EXACT: 0, NORMALIZED: 0, EVIDENCE_RESOLVED: 0, AMBIGUOUS: 0, UNRESOLVED: 0}
    for item in resolutions:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts
