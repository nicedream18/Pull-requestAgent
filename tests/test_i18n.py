from prguard.i18n import localize_finding
from prguard.models import Finding


def test_known_fixture_finding_is_localized_without_changing_evidence():
    finding = Finding(
        category="security",
        severity="critical",
        file_path="app/search.py",
        start_line=1,
        end_line=1,
        title="SQL injection through string interpolation",
        description="User input is interpolated into SQL, allowing query structure to be changed.",
        evidence="cursor.execute(query)",
        suggestion="Bind input values as query parameters.",
        confidence=0.78,
    )

    localized = localize_finding(finding)

    assert localized.evidence == finding.evidence
    assert localized.title == "字符串插值导致 SQL 注入"
    assert "用户输入" in localized.description
    assert "参数化查询" in localized.suggestion


def test_online_review_title_from_pr8426_is_localized():
    finding = Finding(
        category="testing",
        severity="medium",
        file_path="lib/crewai/src/crewai/flow/runtime/_actions.py",
        start_line=1,
        end_line=1,
        title="Default fail-hard branch for tools without explicit policy lacks regression test",
        description="The default branch is not covered.",
        evidence="raise ToolExecutionFailedError(record)",
        suggestion="Add a regression test for the default branch.",
        confidence=0.85,
    )

    localized = localize_finding(finding)

    assert localized.title == "未设置显式策略的默认强制失败分支缺少回归测试"
    assert "默认路径" in localized.description
    assert "增加未设置" in localized.suggestion
