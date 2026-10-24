"""Adversarial safety tests for PathResolver on multi-file PRs.

Every case here is a way the resolver could silently produce a wrong file. The
requirement is that it either resolves correctly or refuses; a confidently wrong answer
is the failure mode these tests exist to prevent.
"""

from __future__ import annotations

import pytest

from prguard.models import Finding
from prguard.paths import (
    AMBIGUOUS,
    EVIDENCE_RESOLVED,
    EXACT,
    NORMALIZED,
    UNRESOLVED,
    PathResolver,
    summarize,
)

MULTI = {
    "app/settings.py": 'TOKEN = "sk-live-aaaa1111"\n',
    "tests/settings.py": 'TOKEN = "sk-live-bbbb2222"\n',
    "app/service.py": "def load():\n    return helper()\n",
    "app/other.py": "def other():\n    return helper()\n",
}


def resolver(sources: dict[str, str] | None = None, renames: dict[str, str] | None = None) -> PathResolver:
    sources = sources or MULTI
    return PathResolver(list(sources), sources, renames)


# --- multi-file identical evidence -------------------------------------------


def test_identical_evidence_in_two_files_is_ambiguous():
    result = resolver().resolve("unknown", 2, "return helper()")
    assert result.status == AMBIGUOUS
    assert result.resolved is None
    assert set(result.candidates) == {"app/service.py", "app/other.py"}


def test_identical_evidence_becomes_resolvable_when_only_one_file_remains():
    sources = {"app/service.py": "def load():\n    return helper()\n"}
    result = PathResolver(list(sources), sources).resolve("unknown", 2, "return helper()")
    assert result.status == EVIDENCE_RESOLVED
    assert result.resolved == "app/service.py"


# --- same basename, different directories ------------------------------------


def test_same_basename_in_two_directories_is_ambiguous():
    result = resolver().resolve("settings.py", 1, 'TOKEN = "sk-live-aaaa1111"')
    assert result.status == AMBIGUOUS
    assert result.resolved is None


def test_qualified_path_disambiguates_the_basename_case():
    assert resolver().resolve("app/settings.py", 1, "").status == EXACT
    assert resolver().resolve("tests/settings.py", 1, "").status == EXACT


# --- same line number, different code ---------------------------------------


def test_same_line_different_code_picks_the_matching_evidence():
    sources = {
        "app/a.py": "import os\n\ndef f(p):\n    os.chmod(p, 0o777)\n",
        "app/b.py": "import os\n\ndef f(p):\n    os.chmod(p, 0o644)\n",
    }
    result = PathResolver(list(sources), sources).resolve("unknown", 4, "os.chmod(p, 0o644)")
    assert result.status == EVIDENCE_RESOLVED
    assert result.resolved == "app/b.py"


# --- invalid path, unique vs non-unique evidence -----------------------------


def test_invalid_path_with_unique_evidence_is_recovered_and_labelled():
    result = resolver().resolve("app/missing.py", 2, "return helper()")
    assert result.status == AMBIGUOUS or result.status == UNRESOLVED
    # Evidence is duplicated, so no file may be guessed.
    assert result.resolved is None


def test_invalid_path_with_unique_evidence_resolves_when_unambiguous():
    sources = {
        "app/service.py": "def load():\n    return helper()\n",
        "app/other.py": "def other():\n    return 1\n",
    }
    result = PathResolver(list(sources), sources).resolve("app/missing.py", 2, "return helper()")
    assert result.status == EVIDENCE_RESOLVED
    assert result.resolved == "app/service.py"
    assert "not a changed file" in result.basis


def test_invalid_path_and_absent_evidence_stays_unresolved():
    result = resolver().resolve("app/missing.py", 2, "text that is nowhere in the diff")
    assert result.status == UNRESOLVED
    assert result.resolved is None


# --- renames -----------------------------------------------------------------


def test_previous_filename_resolves_to_the_new_path():
    sources = {"app/new_name.py": "def load():\n    return 1\n"}
    result = PathResolver(list(sources), sources, {"app/old_name.py": "app/new_name.py"}).resolve(
        "app/old_name.py", 2, "return 1"
    )
    assert result.status == NORMALIZED
    assert result.resolved == "app/new_name.py"
    assert "renamed" in result.basis


def test_rename_of_an_unrelated_path_is_not_accepted():
    sources = {"app/new_name.py": "def load():\n    return 1\n"}
    result = PathResolver(list(sources), sources, {"app/old_name.py": "app/new_name.py"}).resolve(
        "app/other.py", 2, "return 1"
    )
    assert result.resolved != "app/new_name.py" or result.status != NORMALIZED


# --- separators --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "app\\service.py",
        ".\\app\\service.py",
        "app/service.py",
        "./app/service.py",
        "  app/service.py  ",
        "app//service.py",
    ],
)
def test_windows_and_posix_separators_normalize_to_one_path(raw):
    sources = {"app/service.py": "def load():\n    return helper()\n"}
    result = PathResolver(list(sources), sources).resolve(raw, 2, "return helper()")
    assert result.is_resolved
    assert result.resolved == "app/service.py"


def test_drive_letter_is_not_treated_as_a_repository_path():
    sources = {"app/service.py": "def load():\n    return helper()\n"}
    result = PathResolver(list(sources), sources).resolve("C:\\repo\\app\\service.py", 2, "return helper()")
    # It cannot be normalized onto a repo path; evidence may still recover it, but the
    # original must never be echoed back as if it were valid.
    assert result.resolved != "C:\\repo\\app\\service.py"


# --- unreadable, deleted, binary ---------------------------------------------


def test_unreadable_file_keeps_its_exact_path_but_is_rejected_later():
    """Path correctness and evidence correctness are separate concerns.

    A binary or unreadable file can still be named exactly; the resolver must not invent
    a different file for it. Layer 1 is what refuses it, because no line can be verified.
    """
    from prguard.models import PRFile, PRSnapshot
    from prguard.verifier import verify_findings

    sources = {"app/binary.png": "", "app/service.py": "def load():\n    return helper()\n"}
    resolution = PathResolver(list(sources), sources).resolve("app/binary.png", 2, "return helper()")
    assert resolution.status == EXACT
    assert resolution.resolved == "app/binary.png"

    snapshot = PRSnapshot(
        repo="acme/widgets",
        pr_number=1,
        title="binary change",
        base_sha="a",
        head_sha="b",
        files=[PRFile(path="app/binary.png", status="modified", patch="+x", changes=1)],
        diff="diff --git a/app/binary.png b/app/binary.png\n@@ -1 +1 @@\n+x\n",
    )
    finding = Finding(
        category="bug",
        severity="medium",
        file_path="app/binary.png",
        start_line=2,
        end_line=2,
        title="Claim about a binary file",
        description="This cannot be verified because the file is not text.",
        evidence="return helper()",
        suggestion="n/a",
        confidence=0.7,
    )
    verified, rejected = verify_findings(
        [finding], snapshot, sources, check_conditions=False, resolve_paths=True
    )
    assert verified == []
    assert rejected[0]["type"] in {"invalid_line", "evidence_mismatch"}


def test_removed_file_with_no_source_does_not_match_by_evidence():
    sources = {"app/current.py": "def helper():\n    return 2\n"}
    result = PathResolver(list(sources), sources).resolve("app/old.py", 1, "def helper():")
    assert result.resolved != "app/old.py"


def test_missing_source_entry_is_treated_as_unreadable_not_as_a_match():
    result = PathResolver(["app/service.py"], {}).resolve("unknown", 1, "return helper()")
    assert result.status == UNRESOLVED


# --- untrusted content cannot steer the path --------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "app/../../secrets.py",
        "app/service.py\x00.png",
        "app/service.py\napp/evil.py",
        "${HOME}/.ssh/id_rsa",
    ],
)
def test_hostile_paths_never_escape_the_changed_file_set(hostile):
    result = resolver().resolve(hostile, 2, "return helper()")
    assert result.resolved is None or result.resolved in MULTI


def test_resolution_never_trusts_a_path_outside_the_diff():
    result = resolver().resolve("app/not_in_diff.py", 2, "")
    assert result.resolved not in {"app/not_in_diff.py"}


# --- summary accounting ------------------------------------------------------


def test_summary_counts_every_status():
    r = resolver()
    results = [
        r.resolve("app/settings.py", 1, ""),
        r.resolve("./app/service.py", 2, "return helper()"),
        r.resolve("unknown", 2, "return helper()"),
        r.resolve("settings.py", 1, ""),
        r.resolve("unknown", 1, "zzz"),
    ]
    counts = summarize(results)
    assert counts[EXACT] == 1
    assert counts[NORMALIZED] == 1
    assert counts[AMBIGUOUS] == 2
    assert counts[UNRESOLVED] == 1
    assert counts[EVIDENCE_RESOLVED] == 0
