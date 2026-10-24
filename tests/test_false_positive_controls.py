
import pytest

from evals.fixtures import FixtureClient, load_dataset, snapshot_for
from evals.metrics import EVALUATOR_VERSION, _legacy_match_score, evaluate_cases, strict_match
from prguard.evidence import CONTRADICTED, SUPPORTED, UNKNOWN, evaluate_condition
from prguard.models import (
    EvidenceLevel,
    Finding,
    FindingType,
    IssueType,
    PRFile,
    PRSnapshot,
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


def test_test_gap_without_retrieved_tests_stays_unknown():
    case = CASES["test-gap-07"]
    verdict = evaluate_condition(IssueType.MISSING_TEST_COVERAGE, case["code"], case["line_range"][0])
    assert verdict.status == UNKNOWN
    assert "cannot prove absence" in verdict.reason


def test_a_test_file_mentioning_the_function_is_not_coverage():
    """Referencing a function is not evidence that its failure path is tested."""
    case = CASES["test-gap-07"]
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        case["code"],
        case["line_range"][0],
        test_sources={"tests/test_transfers.py": "from app.transfers import transfer_funds\n"},
    )
    assert verdict.status == UNKNOWN
    assert "do not call" in verdict.reason


def test_a_happy_path_test_is_not_failure_path_coverage():
    case = CASES["test-gap-07"]
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        case["code"],
        case["line_range"][0],
        test_sources={"tests/test_transfers.py": "def test_happy():\n    transfer_funds(a, b, 1)\n"},
    )
    assert verdict.status == UNKNOWN
    assert "happy path only" in verdict.reason


@pytest.mark.parametrize(
    "label,source",
    [
        (
            "pytest.raises",
            "import pytest\n\ndef test_failure():\n    with pytest.raises(ValueError):\n"
            "        transfer_funds(a, b, -1)\n",
        ),
        (
            "assertRaises",
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_failure(self):\n"
            "        with self.assertRaises(ValueError):\n            transfer_funds(a, b, -1)\n",
        ),
    ],
)
def test_a_failure_path_assertion_alone_does_not_refute_the_gap(label, source):
    """A raises-block proves *some* failure is tested, not the claimed one."""
    case = CASES["test-gap-07"]
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        case["code"],
        case["line_range"][0],
        test_sources={"tests/test_transfers.py": source},
    )
    assert verdict.status == UNKNOWN, label
    assert "does not name that failure" in verdict.reason


def test_a_failure_path_assertion_refutes_the_gap_only_when_it_matches_the_claim():
    case = CASES["test-gap-07"]
    source = "import pytest\n\ndef test_failure():\n    with pytest.raises(ValueError):\n        transfer_funds(a, b, -1)\n"
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        case["code"],
        case["line_range"][0],
        test_sources={"tests/test_transfers.py": source},
        claim_text="transfer_funds raises ValueError when amount <= 0",
    )
    assert verdict.status == CONTRADICTED
    assert "ValueError" in verdict.reason


def test_a_raised_exception_of_a_different_type_does_not_refute_the_claim():
    case = CASES["test-gap-07"]
    source = "import pytest\n\ndef test_failure():\n    with pytest.raises(KeyError):\n        transfer_funds(a, b, -1)\n"
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        case["code"],
        case["line_range"][0],
        test_sources={"tests/test_transfers.py": source},
        claim_text="transfer_funds raises ValueError when amount <= 0",
    )
    assert verdict.status == UNKNOWN


def test_a_raises_block_around_another_function_does_not_refute_the_gap():
    case = CASES["test-gap-07"]
    source = "import pytest\n\ndef test_other():\n    with pytest.raises(ValueError):\n        charge_card(-1)\n"
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        case["code"],
        case["line_range"][0],
        test_sources={"tests/test_transfers.py": source},
        claim_text="transfer_funds raises ValueError",
    )
    assert verdict.status == UNKNOWN


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
    source = OPTIONAL_GUARD_FORMS[form]
    # The use is the first line that dereferences the optional parameter.
    use_line = next(
        index + 1 for index, line in enumerate(source.splitlines()) if "user.name" in line
    )
    verdict = evaluate_condition(IssueType.NULL_DEREFERENCE, source, use_line)
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
    sources["tests/test_transfers.py"] = (
        "import pytest\n\n"
        "def test_partial_failure():\n"
        "    with pytest.raises(RuntimeError):\n"
        "        transfer_funds(a, b, 1)\n"
    )
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
    # A raises-block exists, but the finding does not name that failure, so the
    # claim stays unverified instead of being refuted.
    assert rejected == []
    assert verified[0].finding_type is FindingType.ADVISORY
    assert "does not name that failure" in (verified[0].rejection_reason or "")


def test_verifier_refutes_the_gap_when_the_claim_names_the_asserted_exception():
    case = CASES["test-gap-07"]
    snapshot, sources = snapshot_for(case)
    sources = dict(sources)
    sources["tests/test_transfers.py"] = (
        "import pytest\n\n"
        "def test_partial_failure():\n"
        "    with pytest.raises(RuntimeError):\n"
        "        transfer_funds(a, b, 1)\n"
    )
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="testing",
        issue_type=IssueType.MISSING_TEST_COVERAGE,
        title="transfer 的 RuntimeError 失败路径缺少测试",
        description="credit 失败时 transfer_funds 会抛出 RuntimeError，但没有测试覆盖。",
        trigger_condition="credit 失败抛 RuntimeError",
    )
    verified, rejected = verify_findings([finding], snapshot, sources)
    assert verified == []
    assert rejected[0]["type"] == "condition_not_met"


def test_verifier_never_mutates_the_input_findings():
    case = CASES["sql-injection-05"]
    snapshot, sources = snapshot_for(case)
    finding = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="Interpolated name reaches the query string.",
        trigger_condition="name comes from the request",
    )
    before = finding.model_dump()
    verified, _ = verify_findings([finding], snapshot, sources)
    assert finding.model_dump() == before
    assert verified[0] is not finding
    assert verified[0].verification_status is VerificationStatus.VERIFIED


def test_verifier_does_not_mutate_input_when_it_demotes():
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
    before = finding.model_dump()
    verified, _ = verify_findings([finding], snapshot, sources)
    assert verified[0].finding_type is FindingType.ADVISORY
    assert finding.model_dump() == before
    assert finding.finding_type is FindingType.DEFECT


def test_flow_keeps_a_pre_verifier_snapshot_independent_of_the_result():
    case = CASES["sql-injection-05"]
    payload = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="Interpolated name reaches the query string.",
        trigger_condition="name comes from the request",
    )
    state = _flow_with_claims("sql-injection-05", [payload], True)
    assert len(state.raw_findings) == len(state.verified_findings) == 1
    assert state.raw_findings[0].verification_status is VerificationStatus.UNCERTAIN
    assert state.verified_findings[0].verification_status is VerificationStatus.VERIFIED
    assert state.raw_findings[0] is not state.verified_findings[0]


def test_pre_and_post_verifier_scores_use_the_same_matcher_and_truth():
    case = CASES["sql-injection-05"]
    payload = make_finding(
        file_path=case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(case, 2),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="Interpolated name reaches the query string.",
        trigger_condition="name comes from the request",
    )
    state = _flow_with_claims("sql-injection-05", [payload], True)
    pre = evaluate_cases([case], {case["case_id"]: list(state.raw_findings)})
    post = evaluate_cases([case], {case["case_id"]: list(state.verified_findings)})
    assert pre["evaluator_version"] == post["evaluator_version"]
    assert pre["tp"] == post["tp"] == 1
    assert pre["fp"] == post["fp"] == 0


def _two_defects_one_safe(safe_line: int, risky_line: int) -> str:
    return (
        "def safe_search(cursor, name):\n"
        "    cursor.execute('SELECT * FROM users WHERE name = %s', (name,))\n"
        "\n"
        "def risky_search(cursor, name):\n"
        "    cursor.execute(f'SELECT * FROM users WHERE name = {name}')\n"
    )


def test_a_real_defect_elsewhere_does_not_leak_into_a_safe_location():
    source = _two_defects_one_safe(2, 5)
    safe = evaluate_condition(IssueType.SQL_INJECTION, source, 2)
    risky = evaluate_condition(IssueType.SQL_INJECTION, source, 5)
    assert safe.status == CONTRADICTED
    assert risky.status == SUPPORTED


def test_verifier_rejects_a_claim_about_the_safe_half_of_a_mixed_file():
    """One file, one genuine defect and one safe call; a finding on the safe call is wrong."""
    source = _two_defects_one_safe(2, 5)
    snapshot = PRSnapshot(
        repo="acme/widgets",
        pr_number=1,
        title="mixed",
        base_sha="base",
        head_sha="head",
        files=[PRFile(path="app/search.py", status="modified", patch="+x", changes=1)],
        diff="diff --git a/app/search.py b/app/search.py\n@@ -1,5 +1,5 @@\n" + "".join(
            f"+{line}\n" for line in source.splitlines()
        ),
    )
    finding = make_finding(
        file_path="app/search.py",
        start_line=2,
        end_line=2,
        evidence="cursor.execute('SELECT * FROM users WHERE name = %s', (name,))",
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL injection in safe_search",
        description="Claimed injection on the parameterized half of the file.",
    )
    verified, rejected = verify_findings([finding], snapshot, {"app/search.py": source})
    assert verified == []
    assert rejected[0]["type"] == "condition_not_met"


def test_off_by_one_without_indexing_is_not_a_defect():
    source = "def loop(items):\n    for i in range(len(items) + 1):\n        print(i)\n"
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == CONTRADICTED
    assert "never used to index" in verdict.reason


def test_off_by_one_with_indexing_is_a_defect():
    source = "def loop(items):\n    for i in range(len(items) + 1):\n        print(items[i])\n"
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED


def test_off_by_one_with_a_bounds_guard_is_not_a_defect():
    source = (
        "def loop(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        if i >= len(items):\n"
        "            break\n"
        "        print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == CONTRADICTED
    assert "dominated by a bounds check" in verdict.reason


def test_ambiguous_location_with_multiple_functions_returns_unknown():
    source = _two_defects_one_safe(2, 5)
    verdict = evaluate_condition(IssueType.SQL_INJECTION, source, None)
    assert verdict.status == UNKNOWN
    assert "does not identify a single function" in verdict.reason


def test_ablation_replays_the_same_raw_findings_for_both_rungs():
    from evals.ablation import _replay_verifier
    from prguard.models import FlowState

    safe_case = CASES["safe-sql-08"]
    risky_case = CASES["sql-injection-05"]
    false_claim = make_finding(
        file_path=safe_case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(safe_case, 2),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="Claimed injection on the parameterized query.",
        trigger_condition="name comes from the request",
    )
    true_claim = make_finding(
        file_path=risky_case["file_path"],
        start_line=2,
        end_line=2,
        evidence=evidence_at(risky_case, 2),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="Interpolated name reaches the query string.",
        trigger_condition="name comes from the request",
    )
    cases = [safe_case, risky_case]
    states = [
        FlowState(raw_findings=[false_claim]),
        FlowState(raw_findings=[true_claim]),
    ]
    frozen = [[item.model_dump() for item in state.raw_findings] for state in states]

    b_scores, b_rejections = _replay_verifier(cases, states, check_conditions=False)
    c_scores, c_rejections = _replay_verifier(cases, states, check_conditions=True)

    # The rungs must not disturb the shared input.
    assert [[item.model_dump() for item in state.raw_findings] for state in states] == frozen
    assert b_scores["evaluator_version"] == c_scores["evaluator_version"]
    # Layer 2 removes the false claim and keeps the true one.
    assert b_rejections["total"] == 0
    assert c_rejections["total"] == 1
    assert c_rejections["by_type"] == {"condition_not_met": 1}
    assert b_scores["fp"] == 1 and c_scores["fp"] == 0
    assert b_scores["tp"] == c_scores["tp"] == 1


MIXED_SQL = (
    "def search(cursor, name, mode):\n"
    "    if mode == 'safe':\n"
    "        cursor.execute('SELECT * FROM users WHERE name = %s', (name,))\n"
    "    else:\n"
    "        cursor.execute(f'SELECT * FROM users WHERE name = {name}')\n"
)
MIXED_SHELL = (
    "import subprocess\n"
    "\n"
    "def run(program, args, user_cmd):\n"
    "    subprocess.run([program, *args], check=True)\n"
    "    subprocess.run(user_cmd, shell=True)\n"
)


def test_same_function_safe_and_risky_sql_are_judged_separately():
    safe = evaluate_condition(IssueType.SQL_INJECTION, MIXED_SQL, 3)
    risky = evaluate_condition(IssueType.SQL_INJECTION, MIXED_SQL, 5)
    assert safe.status == CONTRADICTED
    assert safe.line == 3
    assert risky.status == SUPPORTED
    assert risky.line == 5


def test_same_function_safe_and_risky_subprocess_are_judged_separately():
    safe = evaluate_condition(IssueType.UNSAFE_COMMAND, MIXED_SHELL, 4)
    risky = evaluate_condition(IssueType.UNSAFE_COMMAND, MIXED_SHELL, 5)
    assert safe.status == CONTRADICTED
    assert safe.line == 4
    assert risky.status == SUPPORTED
    assert risky.line == 5


def test_verifier_does_not_borrow_evidence_from_another_call_site():
    """Same file, same function: one finding is right and one must be rejected."""
    snapshot = PRSnapshot(
        repo="acme/widgets",
        pr_number=7,
        title="mixed sql",
        base_sha="base",
        head_sha="head",
        files=[PRFile(path="app/search.py", status="modified", patch="+x", changes=1)],
        diff="diff --git a/app/search.py b/app/search.py\n@@ -1,5 +1,5 @@\n"
        + "".join(f"+{line}\n" for line in MIXED_SQL.splitlines()),
    )
    sources = {"app/search.py": MIXED_SQL}
    lines = MIXED_SQL.splitlines()

    on_risky = make_finding(
        file_path="app/search.py",
        start_line=5,
        end_line=5,
        evidence=lines[4].strip(),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="f-string interpolation reaches the query.",
        trigger_condition="name comes from the request",
    )
    on_safe = make_finding(
        file_path="app/search.py",
        start_line=3,
        end_line=3,
        evidence=lines[2].strip(),
        category="security",
        issue_type=IssueType.SQL_INJECTION,
        title="SQL 注入",
        description="Claimed injection on the parameterized branch.",
        trigger_condition="name comes from the request",
    )

    verified, rejected = verify_findings([on_risky, on_safe], snapshot, sources)
    assert [item.start_line for item in verified] == [5]
    assert rejected[0]["type"] == "condition_not_met"
    # The safe claim must not have been saved or sunk by the risky one.
    assert "parameterized" in rejected[0]["message"] or "bound placeholders" in rejected[0]["message"]


def test_guard_after_the_subscript_does_not_dominate():
    """A bounds check placed after the read cannot protect it."""
    source = (
        "def loop(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        print(items[i])\n"
        "        if i >= len(items):\n"
        "            break\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED
    assert "one past the end" in verdict.reason


def test_guard_before_the_subscript_dominates():
    source = (
        "def loop(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        if i >= len(items):\n"
        "            break\n"
        "        print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == CONTRADICTED


def test_scoped_single_call_still_matches_a_nearby_reported_line():
    """A finding on the enclosing statement is still tied to the only call."""
    case = CASES["sql-injection-05"]
    verdict = evaluate_condition(IssueType.SQL_INJECTION, case["code"], 1)
    assert verdict.status == SUPPORTED
    assert verdict.line == 2


def test_far_off_line_is_not_tied_to_an_unrelated_call():
    case = CASES["sql-injection-05"]
    verdict = evaluate_condition(IssueType.SQL_INJECTION, case["code"], 40)
    assert verdict.status == UNKNOWN


def test_kwargs_unpacking_does_not_clear_a_shell_claim():
    """`**opts` may carry shell=True, so the verifier must not refute the claim."""
    source = "import subprocess\n\ndef run(cmd, **opts):\n    subprocess.run(cmd, **opts)\n"
    verdict = evaluate_condition(IssueType.UNSAFE_COMMAND, source, 4)
    assert verdict.status == UNKNOWN
    assert "keyword-argument unpacking" in verdict.reason


# --- boundary guard soundness ------------------------------------------------


def test_inclusive_positive_guard_does_not_protect_the_index_equal_to_length():
    """`if i <= len(x)` admits the out-of-range iteration, so it is not a guard."""
    source = (
        "def f(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        if i <= len(items):\n"
        "            print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED


def test_reject_guard_with_strict_greater_than_does_not_protect():
    """`if i > len(x): break` lets i == len(x) through, which is the bad value."""
    source = (
        "def f(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        if i > len(items):\n"
        "            break\n"
        "        print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED


@pytest.mark.parametrize(
    "label,guard",
    [
        ("greater-or-equal", "if i >= len(items):\n            break"),
        ("greater-than-len-minus-1", "if i > len(items) - 1:\n            break"),
        ("equal-to-length", "if i == len(items):\n            break"),
    ],
)
def test_sufficient_reject_guards_are_recognised(label, guard):
    source = f"def f(items):\n    for i in range(len(items) + 1):\n        {guard}\n        print(items[i])\n"
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == CONTRADICTED, label


def test_a_guard_covering_only_one_of_two_reads_does_not_protect():
    """The unguarded second read can still go out of range."""
    source = (
        "def f(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        if i < len(items):\n"
        "            print(items[i])\n"
        "        print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED


def test_a_guard_placed_after_the_read_does_not_protect():
    source = (
        "def f(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        print(items[i])\n"
        "        if i >= len(items):\n"
        "            break\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED


def test_a_break_inside_a_nested_loop_does_not_exit_the_analysed_loop():
    """The break leaves the inner `while`, so the outer iteration still reads."""
    source = (
        "def f(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        while True:\n"
        "            if i >= len(items):\n"
        "                break\n"
        "            break\n"
        "        print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED


def test_a_conditional_break_does_not_count_as_always_exiting():
    """`if i >= len(x): if strict: break` does not exit on every path."""
    source = (
        "def f(items, strict):\n"
        "    for i in range(len(items) + 1):\n"
        "        if i >= len(items):\n"
        "            if strict:\n"
        "                break\n"
        "        print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == SUPPORTED


def test_a_guard_whose_else_branch_also_exits_is_accepted():
    source = (
        "def f(items):\n"
        "    for i in range(len(items) + 1):\n"
        "        if i >= len(items):\n"
        "            break\n"
        "        else:\n"
        "            continue\n"
        "        print(items[i])\n"
    )
    verdict = evaluate_condition(IssueType.BOUNDARY_CONDITION, source, 2)
    assert verdict.status == CONTRADICTED


# --- test coverage: reference is not coverage --------------------------------


@pytest.mark.parametrize(
    "label,test_source,claim,expected",
    [
        (
            "raises, no claim text",
            "import pytest\ndef test_x():\n    with pytest.raises(ValueError):\n        transfer_funds(a, b, -1)\n",
            "",
            UNKNOWN,
        ),
        (
            "raises, claim names it",
            "import pytest\ndef test_x():\n    with pytest.raises(ValueError):\n        transfer_funds(a, b, -1)\n",
            "transfer_funds raises ValueError",
            CONTRADICTED,
        ),
        (
            "happy path only",
            "def test_x():\n    transfer_funds(a, b, 1)\n",
            "transfer_funds raises ValueError",
            UNKNOWN,
        ),
        (
            "bare mention",
            "from app.transfers import transfer_funds\n",
            "transfer_funds raises ValueError",
            UNKNOWN,
        ),
    ],
)
def test_coverage_levels_require_the_claimed_failure(label, test_source, claim, expected):
    risky = "def transfer_funds(source, target, amount):\n    source.debit(amount)\n    target.credit(amount)\n"
    verdict = evaluate_condition(
        IssueType.MISSING_TEST_COVERAGE,
        risky,
        1,
        test_sources={"tests/test_transfers.py": test_source},
        claim_text=claim,
    )
    assert verdict.status == expected, label
