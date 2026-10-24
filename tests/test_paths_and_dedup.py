"""Safety properties of PathResolver and the finding deduplicator.

The resolver may only recover a path from trusted PR data, and the deduplicator must
merge different wordings of one defect without ever merging two distinct defects.
"""

from __future__ import annotations

import pytest

from prguard.models import Finding, IssueType
from prguard.paths import (
    AMBIGUOUS,
    EVIDENCE_RESOLVED,
    EXACT,
    NORMALIZED,
    UNRESOLVED,
    PathResolver,
)
from prguard.verifier import _same_issue

SOURCES = {
    "app/service.py": "def load(path):\n    handle = open(path)\n    return handle.read()\n",
    "app/utils.py": "def helper():\n    return 42\n",
    "docs/usage.md": "# Usage\n\nRun the tool.\n",
}


def resolver(paths: list[str] | None = None) -> PathResolver:
    return PathResolver(paths or list(SOURCES), SOURCES)


# --- exact and normalized ----------------------------------------------------


def test_exact_match_is_reported_as_exact():
    result = resolver().resolve("app/service.py", 2, "handle = open(path)")
    assert result.status == EXACT
    assert result.resolved == "app/service.py"


@pytest.mark.parametrize(
    "raw",
    ["  app/service.py  ", "./app/service.py", "a/app/service.py", "b/app/service.py", "app//service.py", "app/service.py/"],
)
def test_separator_and_prefix_noise_is_normalized(raw):
    result = resolver().resolve(raw, 2, "handle = open(path)")
    assert result.status == NORMALIZED
    assert result.resolved == "app/service.py"


def test_unique_basename_is_accepted():
    result = resolver().resolve("service.py", 2, "handle = open(path)")
    assert result.status == NORMALIZED
    assert result.resolved == "app/service.py"


def test_duplicate_basenames_are_ambiguous_not_guessed():
    """Two files named settings.py must never be silently collapsed."""
    sources = {
        "app/settings.py": "TOKEN = 'x'\n",
        "tests/settings.py": "TOKEN = 'y'\n",
    }
    result = PathResolver(list(sources), sources).resolve("settings.py", 1, "TOKEN = 'x'")
    assert result.status == AMBIGUOUS
    assert result.resolved is None
    assert set(result.candidates) == {"app/settings.py", "tests/settings.py"}


def test_a_specific_but_unknown_path_is_not_repaired_by_name():
    """`app/missing.py` must not become `app/service.py` just because it is close."""
    result = resolver().resolve("app/missing.py", 2, "handle = open(path)")
    # Evidence does appear in exactly one file, so recovery is allowed, but it must be
    # labelled as evidence-based, never as an exact or normalized match.
    assert result.status == EVIDENCE_RESOLVED
    assert result.resolved == "app/service.py"
    assert "not a changed file" in result.basis


def test_unknown_path_without_evidence_stays_unresolved():
    result = resolver().resolve("unknown", 2, "text that appears nowhere")
    assert result.status == UNRESOLVED
    assert result.resolved is None


def test_unknown_path_is_not_resolved_just_because_one_file_exists():
    """The single-candidate shortcut is exactly what must not exist."""
    result = resolver().resolve("unknown", 2, "evidence absent from the only file")
    assert result.status == UNRESOLVED


def test_short_evidence_cannot_identify_a_file():
    result = resolver().resolve("unknown", 2, "x = 1")
    assert result.status == UNRESOLVED
    assert "too short" in result.basis


def test_evidence_that_appears_in_two_files_is_ambiguous():
    sources = {
        "app/a.py": "def f():\n    return helper()\n",
        "app/b.py": "def g():\n    return helper()\n",
    }
    result = PathResolver(list(sources), sources).resolve("unknown", 2, "return helper()")
    assert result.status == AMBIGUOUS
    assert result.resolved is None


def test_line_must_agree_with_where_the_evidence_sits():
    result = resolver().resolve("unknown", 9, "handle = open(path)")
    assert result.status == UNRESOLVED
    assert "cites line 9" in result.basis


def test_missing_line_is_not_enough_for_evidence_recovery():
    result = resolver().resolve("unknown", None, "handle = open(path)")
    assert result.status == UNRESOLVED
    assert "no line was reported" in result.basis


def test_placeholder_with_parenthetical_note_is_treated_as_placeholder():
    result = resolver().resolve("unknown (implementation not shown)", 2, "handle = open(path)")
    assert result.status == EVIDENCE_RESOLVED
    assert result.resolved == "app/service.py"


def test_resolver_ignores_files_outside_the_changed_set():
    """Retrieved context files are not evidence for a changed-file path."""
    result = PathResolver(["app/service.py"], SOURCES).resolve("utils.py", 2, "return 42")
    assert result.status == UNRESOLVED


def test_resolution_preserves_the_original_value():
    result = resolver().resolve("unknown", 2, "handle = open(path)")
    assert result.original == "unknown"
    assert result.as_dict()["original"] == "unknown"
    assert result.as_dict()["status"] == EVIDENCE_RESOLVED


# --- deduplication -----------------------------------------------------------


def finding(**overrides) -> Finding:
    payload = {
        "category": "security",
        "severity": "high",
        "file_path": "app/service.py",
        "start_line": 2,
        "end_line": 2,
        "title": "default title",
        "description": "default description text that is long enough",
        "evidence": "handle = open(path)",
        "suggestion": "close it",
        "confidence": 0.8,
        "issue_type": IssueType.RESOURCE_LEAK,
        "trigger_condition": "read raises",
    }
    payload.update(overrides)
    # Keep end_line consistent with whatever start_line the test asked for.
    if "start_line" in overrides and "end_line" not in overrides:
        payload["end_line"] = payload["start_line"]
    return Finding(**payload)


def test_same_defect_different_chinese_wording_merges():
    left = finding(title="文件句柄未关闭，存在资源泄漏风险", description="open 之后从未 close，异常路径也会泄漏句柄。")
    right = finding(title="导出函数没有释放文件句柄", description="读取完成后句柄保持打开，长时间运行会耗尽文件描述符。")
    assert _same_issue(left, right)


def test_same_defect_reported_by_two_agents_merges():
    left = finding(source_agent="CodeQualityAgent")
    right = finding(source_agent="SecurityAgent", title="完全不同的标题措辞", description="另一段完全不同的中文描述内容。")
    assert _same_issue(left, right)


def test_two_distinct_defects_in_one_function_do_not_merge():
    left = finding(issue_type=IssueType.RESOURCE_LEAK, title="句柄泄漏")
    right = finding(issue_type=IssueType.MISSING_EXCEPTION_HANDLING, title="缺少异常处理")
    assert not _same_issue(left, right)


def test_same_line_different_issue_type_does_not_merge():
    left = finding(start_line=4, issue_type=IssueType.SQL_INJECTION, title="SQL 注入风险")
    right = finding(start_line=4, issue_type=IssueType.HARDCODED_SECRET, title="硬编码密钥泄露")
    assert not _same_issue(left, right)


def test_similar_description_with_different_evidence_and_trigger_does_not_merge():
    left = finding(
        evidence="handle = open(path)",
        trigger_condition="when the path does not exist",
        description="文件句柄未关闭导致资源泄漏，异常路径也会泄漏句柄。",
    )
    right = finding(
        evidence="return handle.read()",
        trigger_condition="当并发写入时缓存失效",
        description="文件句柄未关闭导致资源泄漏，长时间运行会耗尽描述符。",
    )
    assert not _same_issue(left, right)


def test_identical_citation_merges_even_when_trigger_wording_differs():
    """Real duplicates cite the same code line but describe the trigger differently."""
    left = finding(
        evidence="    os.chmod(path, 0o777)",
        trigger_condition="调用 save(path) 时 path 指向不存在的文件",
        title="权限被设置为全局可写",
    )
    right = finding(
        evidence="    os.chmod(path, 0o777)",
        trigger_condition="任何上传流程调用 save(path) 时都会触发",
        title="save 对任意路径设置 0o777",
    )
    assert _same_issue(left, right)


def test_far_apart_lines_do_not_merge():
    left = finding(start_line=2)
    right = finding(start_line=40)
    assert not _same_issue(left, right)


def test_different_files_do_not_merge():
    left = finding(file_path="app/service.py")
    right = finding(file_path="app/utils.py")
    assert not _same_issue(left, right)


def test_equivalent_paths_do_merge():
    left = finding(file_path="app/service.py")
    right = finding(file_path="./app/service.py")
    assert _same_issue(left, right)
