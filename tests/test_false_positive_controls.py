import pytest

from evals.fixtures import FixtureClient, load_dataset, snapshot_for
from evals.metrics import EVALUATOR_VERSION, _legacy_match_score, evaluate_cases, strict_match
from prguard.evidence import CONTRADICTED, SUPPORTED, UNKNOWN, evaluate_condition
from prguard.models import (
    EvidenceLevel,
    Finding,
    FindingType,
    IssueType,
    VerificationStatus,
)
from prguard.verifier import changed_lines, verify_findings

CASES = {case["case_id"]: case for case in load_dataset()["cases"]}


def evidence_at(case: dict, line: int) -> str:
    return case["code"].splitlines()[line - 1].strip()


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


def test_test_gap_needs_test_suite_context_and_stays_unknown():
    case = CASES["test-gap-07"]
    verdict = evaluate_condition(IssueType.MISSING_TEST_COVERAGE, case["code"], case["line_range"][0])
    assert verdict.status == UNKNOWN
    assert "does not enumerate the test suite" in verdict.reason


def test_test_gap_is_refuted_when_tests_are_supplied():
    case = CASES["test-gap-07"]
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        case["code"],
        case["line_range"][0],
        test_paths_for_module={"tests/test_transfers.py"},
    )
    assert verdict.status == CONTRADICTED


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


def test_condition_check_is_unknown_without_source():
    assert evaluate_condition(IssueType.SQL_INJECTION, "", 1).status == UNKNOWN


def test_test_gap_without_behavior_risk_is_refuted():
    verdict = evaluate_condition(IssueType.MISSING_TEST_COVERAGE, "def add(a, b):\n    return a + b\n", 1)
    assert verdict.status == CONTRADICTED


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


def test_verifier_downgrades_unknown_defect_to_advisory():
    case = CASES["test-gap-07"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="testing",
        issue_type=IssueType.MISSING_TEST_COVERAGE,
        title="transfer 缺少失败路径测试",
        description="debit 与 credit 之间存在部分失败风险，但没有测试覆盖。",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert rejected == []
    assert verified[0].finding_type is FindingType.ADVISORY
    assert verified[0].verification_status is VerificationStatus.UNCERTAIN
    assert verified[0].needs_human_review is True


def test_defect_without_trigger_condition_is_flagged_by_schema():
    finding = make_finding(trigger_condition="")
    # The model must not reclassify recorded data; it only flags it.
    assert finding.finding_type is FindingType.DEFECT
    assert finding.needs_human_review is True


def test_verifier_demotes_defect_without_trigger_condition():
    case = CASES["null-deref-01"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=4,
        end_line=4,
        evidence=evidence_at(case, 4),
        issue_type=IssueType.NULL_DEREFERENCE,
        trigger_condition="",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert rejected == []
    assert verified[0].finding_type is FindingType.ADVISORY
    assert "trigger" in (verified[0].rejection_reason or "").lower()


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
        evidence=evidence_at(case, 4),
        category="security",
        issue_type=IssueType.OVERLY_PERMISSIVE_FILE_MODE,
        title="文件权限被设置为全局可写可执行",
        description="mode 0o777 授予所有用户写权限。",
    )
    assert strict_match(finding, case) == "structured"


def test_text_only_match_still_requires_semantic_overlap():
    case = CASES["permissions-11"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=4,
        end_line=4,
        evidence=evidence_at(case, 4),
        category="security",
        issue_type=IssueType.OTHER,
        title="完全无关的问题",
        description="这条描述与标注缺陷没有任何共同词汇。",
    )
    assert strict_match(finding, case) is None


def test_unlocated_finding_is_not_a_detection_by_default():
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
    assert strict_match(finding, case) is None


def test_unlocated_finding_matches_only_with_an_explicit_label_exception():
    case = dict(CASES["test-gap-07"], allow_unlocated=True)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=None,
        end_line=None,
        evidence="def transfer_funds(source, target, amount):",
        category="testing",
        issue_type=IssueType.MISSING_TEST_COVERAGE,
        title="transfer 失败路径缺少测试",
        description="缺少 debit 或 credit 失败时的回滚测试。",
    )
    assert strict_match(finding, case) == "structured"


def test_correct_issue_type_with_wrong_line_is_not_a_detection():
    case = CASES["null-deref-01"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=40,
        end_line=40,
        evidence="return user.name",
        issue_type=IssueType.NULL_DEREFERENCE,
    )
    assert strict_match(finding, case) is None


def test_correct_issue_type_without_source_evidence_is_not_a_detection():
    case = CASES["null-deref-01"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=4,
        end_line=4,
        evidence="this text is not in the file",
        issue_type=IssueType.NULL_DEREFERENCE,
    )
    assert strict_match(finding, case) is None


def test_correct_location_and_evidence_but_wrong_issue_type_is_not_a_detection():
    case = CASES["sql-injection-05"]
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="security",
        issue_type=IssueType.DIVISION_BY_ZERO,
        title="division by zero in search",
        description="claim that does not match the labelled defect type",
    )
    assert strict_match(finding, case) is None


def test_advisory_matching_the_defect_label_is_not_a_true_positive():
    case = CASES["sql-injection-05"]
    advisory = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        finding_type=FindingType.ADVISORY,
        title="SQL 注入",
        description="Issue type and location are correct but this is marked advisory.",
    )
    result = evaluate_cases([case], {case["case_id"]: [advisory]})
    assert result["tp"] == 0
    assert result["strict_tp"] == 0
    assert result["strict_fp"] == 1
    assert result["strict_precision"] == 0.0
    assert result["defect_precision"] == 0.0


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


def test_relabelling_a_true_positive_as_advisory_cannot_raise_strict_f1():
    case = CASES["sql-injection-05"]
    payload = dict(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="Interpolated name reaches the query string.",
    )
    as_defect = evaluate_cases([case], {case["case_id"]: [make_finding(**payload)]})
    as_advisory = evaluate_cases(
        [case], {case["case_id"]: [make_finding(**payload, finding_type=FindingType.ADVISORY)]}
    )
    assert as_defect["strict_f1"] > as_advisory["strict_f1"]
    assert as_advisory["strict_f1"] == 0.0


def test_one_truth_can_only_be_claimed_once():
    case = CASES["sql-injection-05"]
    duplicated = [
        make_finding(
            file_path=case["file_path"],
            start_line=2,
            end_line=2,
            evidence=evidence_at(case, 2),
            category="security",
            issue_type=IssueType.SQL_INJECTION,
            title="SQL 注入 A",
            description="Same defect reported twice with a different title.",
        ),
        make_finding(
            file_path=case["file_path"],
            start_line=2,
            end_line=2,
            evidence=evidence_at(case, 2),
            category="security",
            issue_type=IssueType.SQL_INJECTION,
            title="SQL 注入 B",
            description="Same defect reported twice, different wording again.",
        ),
    ]
    result = evaluate_cases([case], {case["case_id"]: duplicated})
    assert result["tp"] == 1
    assert result["fp"] == 1


def test_multiple_candidate_findings_score_one_truth_once():
    case = CASES["permissions-11"]
    candidates = [
        make_finding(
            file_path=case["file_path"],
            start_line=4,
            end_line=4,
            evidence=evidence_at(case, 4),
            category="security",
            issue_type=IssueType.OVERLY_PERMISSIVE_FILE_MODE,
            title="0o777 权限过宽",
            description="World-writable mode on the uploaded file.",
        ),
        make_finding(
            file_path=case["file_path"],
            start_line=3,
            end_line=3,
            evidence=evidence_at(case, 3),
            category="bug",
            issue_type=IssueType.MISSING_EXCEPTION_HANDLING,
            title="save 缺少异常处理",
            description="chmod can raise OSError and nothing handles it.",
        ),
        make_finding(
            file_path=case["file_path"],
            start_line=1,
            end_line=1,
            evidence=evidence_at(case, 1),
            category="quality",
            issue_type=IssueType.OTHER,
            finding_type=FindingType.ADVISORY,
            title="缺少 docstring",
            description="Add a docstring to explain the permission change.",
        ),
    ]
    result = evaluate_cases([case], {case["case_id"]: candidates})
    assert result["tp"] == 1
    assert result["fp"] == 1
    assert result["advisory_count"] == 1
    assert result["strict_fp"] == 2


def test_legacy_matcher_still_credits_unlocated_structured_matches():
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
    assert _legacy_match_score(finding, case) is not None
    assert strict_match(finding, case) is None


def test_evaluate_cases_reports_both_evaluator_views():
    result = evaluate_cases([], {})
    assert result["evaluator_version"] == EVALUATOR_VERSION
    for key in ("legacy_precision", "defect_precision", "strict_precision"):
        assert key in result


def _flow_with_claims(case_id: str, findings: list[Finding], condition_checks: bool):
    from prguard.flow import ReviewFlow
    from prguard.models import AgentRun, PRReviewRequest

    case = CASES[case_id]
    snapshot, sources = snapshot_for(case)

    async def runner(agent, context, model, *, semaphore, timeout_seconds):
        del context, model, semaphore, timeout_seconds
        payload = findings if agent == "SecurityAgent" else []
        return AgentRun(
            agent=agent,
            status="success",
            started_at="now",
            ended_at="now",
            calls=1,
            findings=[item.model_copy(update={"source_agent": agent}) for item in payload],
        )

    request = PRReviewRequest(
        repo=snapshot.repo,
        pr_number=snapshot.pr_number,
        strategy="static",
        mode="offline",
        output_dir="/private/tmp/prguard-ablation",
        condition_checks=condition_checks,
    )
    flow = ReviewFlow(github_client=FixtureClient(snapshot, sources), agent_runner=runner)
    flow.kickoff(inputs={"request": request.model_dump()})
    return flow.state


def _false_sql_claim() -> Finding:
    case = CASES["safe-sql-08"]
    return make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL injection in search",
        description="Claimed injection on a parameterized query.",
        evidence="cursor.execute('SELECT * FROM users WHERE name = %s', (name,))",
    )


def test_condition_checks_ablation_changes_the_outcome():
    with_checks = _flow_with_claims("safe-sql-08", [_false_sql_claim()], True)
    without_checks = _flow_with_claims("safe-sql-08", [_false_sql_claim()], False)

    assert with_checks.verified_findings == []
    assert any(error["type"] == "condition_not_met" for error in with_checks.errors)

    assert len(without_checks.verified_findings) == 1
    assert not any(error["type"] == "condition_not_met" for error in without_checks.errors)


def test_flow_records_verifier_before_and_after_counts():
    state = _flow_with_claims("safe-sql-08", [_false_sql_claim()], True)
    assert state.metrics["raw_findings"] == 1
    assert state.metrics["verified_findings"] == 0
    assert state.metrics["rejected_findings"] == 1


OPTIONAL_GUARD_FORMS = {
    "raise_on_none": (
        "from typing import Optional\n\n"
        "def name_of(user: Optional[object]) -> str:\n"
        "    if user is None:\n"
        "        raise ValueError('missing user')\n"
        "    return user.name\n"
    ),
    "early_return": (
        "from typing import Optional\n\n"
        "def name_of(user: Optional[object]) -> str:\n"
        "    if not user:\n"
        "        return ''\n"
        "    return user.name\n"
    ),
    "wrapped_in_positive_if": (
        "from typing import Optional\n\n"
        "def name_of(user: Optional[object]) -> str:\n"
        "    if user is not None:\n"
        "        return user.name\n"
        "    return ''\n"
    ),
    "assert_guard": (
        "from typing import Optional\n\n"
        "def name_of(user: Optional[object]) -> str:\n"
        "    assert user is not None\n"
        "    return user.name\n"
    ),
}


@pytest.mark.parametrize("form", sorted(OPTIONAL_GUARD_FORMS))
def test_optional_none_guards_are_recognised(form):
    verdict = evaluate_condition(IssueType.NULL_DEREFERENCE, OPTIONAL_GUARD_FORMS[form], 6)
    assert verdict.status == CONTRADICTED, form


def test_optional_dereference_before_the_guard_is_still_supported():
    source = (
        "from typing import Optional\n\n"
        "def name_of(user: Optional[object]) -> str:\n"
        "    first = user.name\n"
        "    if user is None:\n"
        "        return ''\n"
        "    return first\n"
    )
    verdict = evaluate_condition(IssueType.NULL_DEREFERENCE, source, 4)
    assert verdict.status == SUPPORTED


def test_safe_subprocess_is_refuted_at_verifier_level():
    case = CASES["safe-subprocess-13"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=4,
        end_line=4,
        evidence=evidence_at(case, 4),
        category="security",
        issue_type=IssueType.UNSAFE_COMMAND,
        title="command injection in run",
        description="Claimed shell injection on an argument-vector call.",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert verified == []
    assert rejected[0]["type"] == "condition_not_met"


def test_unknown_context_keeps_a_defect_out_of_the_confirmed_set():
    case = CASES["test-gap-07"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="testing",
        issue_type=IssueType.MISSING_TEST_COVERAGE,
        title="transfer 缺少失败路径测试",
        description="debit 与 credit 之间存在部分失败风险，但没有测试覆盖。",
    )
    verified, _ = verify_findings([finding], snapshot, sources)
    assert verified[0].finding_type is FindingType.ADVISORY
    assert verified[0].verification_status is VerificationStatus.UNCERTAIN


def test_test_gap_is_refuted_when_the_context_contains_the_test_file():
    case = CASES["test-gap-07"]
    snapshot, sources = snapshot_for(case)
    sources = dict(sources)
    sources["tests/test_transfers.py"] = "from app.transfers import transfer_funds\n\ndef test_partial_failure():\n    ...\n"
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="testing",
        issue_type=IssueType.MISSING_TEST_COVERAGE,
        title="transfer 缺少失败路径测试",
        description="debit 与 credit 之间存在部分失败风险，但没有测试覆盖。",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert verified == []
    assert rejected[0]["type"] == "condition_not_met"
