import pytest

from evals.fixtures import load_dataset, snapshot_for
from evals.metrics import _match_score, evaluate_cases
from prguard.evidence import CONTRADICTED, SUPPORTED, UNDETERMINED, evaluate_condition
from prguard.models import (
    EvidenceLevel,
    Finding,
    FindingType,
    IssueType,
    VerificationStatus,
)
from prguard.verifier import changed_lines, verify_findings

CASES = {case["case_id"]: case for case in load_dataset()["cases"]}


def make_finding(**overrides) -> Finding:
    payload = {
        "category": "bug",
        "severity": "high",
        "file_path": "app/x.py",
        "start_line": 1,
        "end_line": 1,
        "title": "Concrete defect title",
        "description": "A concrete defect with a stated trigger.",
        "evidence": "return user.name",
        "suggestion": "Guard the value.",
        "confidence": 0.9,
        "trigger_condition": "user is None",
        "issue_type": IssueType.NULL_DEREFERENCE,
        "finding_type": FindingType.DEFECT,
    }
    payload.update(overrides)
    return Finding(**payload)


@pytest.mark.parametrize(
    "case_id,issue_type,expected",
    [
        ("null-deref-01", IssueType.NULL_DEREFERENCE, SUPPORTED),
        ("exception-02", IssueType.MISSING_EXCEPTION_HANDLING, SUPPORTED),
        ("boundary-03", IssueType.BOUNDARY_CONDITION, SUPPORTED),
        ("resource-leak-04", IssueType.RESOURCE_LEAK, SUPPORTED),
        ("sql-injection-05", IssueType.SQL_INJECTION, SUPPORTED),
        ("shell-injection-06", IssueType.UNSAFE_COMMAND, SUPPORTED),
        ("test-gap-07", IssueType.MISSING_TEST_COVERAGE, SUPPORTED),
        ("permissions-11", IssueType.OVERLY_PERMISSIVE_FILE_MODE, SUPPORTED),
        ("division-zero-12", IssueType.DIVISION_BY_ZERO, SUPPORTED),
        ("secret-14", IssueType.HARDCODED_SECRET, SUPPORTED),
    ],
)
def test_condition_check_confirms_every_labeled_defect(case_id, issue_type, expected):
    case = CASES[case_id]
    verdict = evaluate_condition(issue_type, case["code"], case["line_range"][0])
    assert verdict.status == expected
    assert verdict.reason


@pytest.mark.parametrize(
    "case_id,issue_type",
    [
        ("safe-sql-08", IssueType.SQL_INJECTION),
        ("safe-subprocess-13", IssueType.UNSAFE_COMMAND),
    ],
)
def test_condition_check_refutes_safe_counterexamples(case_id, issue_type):
    case = CASES[case_id]
    verdict = evaluate_condition(issue_type, case["code"], case["line_range"][0])
    assert verdict.status == CONTRADICTED


def test_condition_check_refuses_to_prove_keyword_hits():
    """A file that merely mentions a word must not be treated as a proven defect."""
    verdict = evaluate_condition(IssueType.SQL_INJECTION, "NOTE = 'sql_injection appears here only'\n", 1)
    assert verdict.status == CONTRADICTED


def test_condition_check_is_undetermined_without_source():
    assert evaluate_condition(IssueType.SQL_INJECTION, "", 1).status == UNDETERMINED


def test_test_gap_without_behavior_risk_is_not_a_defect():
    verdict = evaluate_condition(IssueType.MISSING_TEST_COVERAGE, "def add(a, b):\n    return a + b\n", 1)
    assert verdict.status == UNDETERMINED


def test_verifier_rejects_claim_that_condition_refutes():
    case = CASES["safe-sql-08"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence="cursor.execute('SELECT * FROM users WHERE name = %s', (name,))",
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL injection in search",
        description="Claimed injection on a parameterized query.",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert verified == []
    assert rejected[0]["type"] == "condition_not_met"


def test_verifier_keeps_proven_defect_as_verified():
    case = CASES["sql-injection-05"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence='cursor.execute(f"SELECT * FROM users WHERE name = \'{name}\'")',
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL injection in search",
        description="Interpolated name reaches the query string.",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert rejected == []
    assert verified[0].verification_status is VerificationStatus.VERIFIED
    assert verified[0].evidence_level is EvidenceLevel.EXACT


def test_verifier_downgrades_undetermined_defect_to_advisory():
    case = CASES["normal-pr-15"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=1,
        end_line=1,
        evidence="def clamp(value, low, high):",
        category="testing",
        issue_type=IssueType.MISSING_TEST_COVERAGE,
        title="clamp 没有任何测试覆盖",
        description="新增函数缺少测试，但代码本身没有多资源副作用。",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert rejected == []
    assert verified[0].finding_type is FindingType.ADVISORY
    assert verified[0].verification_status is VerificationStatus.UNCERTAIN
    assert verified[0].needs_human_review is True


def test_defect_without_trigger_condition_is_demoted_by_schema():
    finding = make_finding(trigger_condition="")
    assert finding.finding_type is FindingType.ADVISORY
    assert finding.needs_human_review is True


def test_changed_lines_reads_new_side_of_the_patch():
    case = CASES["sql-injection-05"]
    snapshot, _ = snapshot_for(case)
    assert changed_lines(snapshot)[case["file_path"]] == {1, 2}


def test_structured_match_ignores_label_language():
    case = CASES["permissions-11"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=4,
        end_line=4,
        category="security",
        issue_type=IssueType.OVERLY_PERMISSIVE_FILE_MODE,
        title="文件权限被设置为全局可写可执行",
        description="mode 0o777 授予所有用户写权限。",
    )
    assert _match_score(finding, case) is not None


def test_text_only_match_still_requires_semantic_overlap():
    case = CASES["permissions-11"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=4,
        end_line=4,
        category="security",
        issue_type=IssueType.OTHER,
        title="完全无关的问题",
        description="这条描述与标注缺陷没有任何共同词汇。",
    )
    assert _match_score(finding, case) is None


def test_finding_without_line_can_match_structurally():
    case = CASES["test-gap-07"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=None,
        end_line=None,
        category="testing",
        issue_type=IssueType.MISSING_TEST_COVERAGE,
        title="transfer 失败路径缺少测试",
        description="缺少 debit 或 credit 失败时的回滚测试。",
    )
    assert _match_score(finding, case) is not None


def test_line_outside_window_never_matches():
    case = CASES["null-deref-01"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=40,
        end_line=40,
        issue_type=IssueType.NULL_DEREFERENCE,
    )
    assert _match_score(finding, case) is None


def test_advisories_are_excluded_from_precision_but_counted_strictly():
    case = CASES["normal-pr-10"]
    advisory = make_finding(
        file_path=case["file_path"],
        start_line=1,
        category="quality",
        issue_type=IssueType.OTHER,
        title="Add a docstring",
        description="Documentation would help the next reader.",
        finding_type=FindingType.ADVISORY,
    )
    result = evaluate_cases([case], {case["case_id"]: [advisory]})
    assert result["fp"] == 0
    assert result["advisory_count"] == 1
    assert result["advisory_rate"] == 1.0
    assert result["strict_precision"] == 0.0


def test_labelling_everything_advisory_does_not_hide_false_positives():
    case = CASES["normal-pr-10"]
    findings = [
        make_finding(
            file_path=case["file_path"],
            start_line=1,
            category="bug",
            issue_type=IssueType.OTHER,
            title=f"Advisory {index}",
            description="Unproven suggestion that would otherwise be a false positive.",
            finding_type=FindingType.ADVISORY,
        )
        for index in range(3)
    ]
    result = evaluate_cases([case], {case["case_id"]: findings})
    assert result["defect_precision"] == 0.0
    assert result["strict_precision"] == 0.0
    assert result["advisory_rate"] == 1.0
