"""Development schedule and independent fixed HTTP oracle are intentionally narrow."""

from collections import Counter

import pytest

from agenttest.paired_baseline import fixed_case
from agenttest.paired_tasks import paired_cases, paired_inputs


def test_schedule_is_balanced_fresh_and_keeps_insufficient_case():
    cases = paired_cases()
    assert len(cases) == len({case['id'] for case in cases}) == 24
    assert Counter(case['category'] for case in cases) == {
        'normal': 6, 'invalid_parameter': 6, 'permission': 6, 'resource_state': 6}
    assert sum(case['fault'] != 'none' for case in cases) == 8
    assert sum(case['finding'] == 'EVIDENCE_INSUFFICIENT' for case in cases) == 1
    tasks = [paired_inputs(case)[5] for case in cases]
    assert len({task.user_task for task in tasks}) == 24


@pytest.mark.asyncio
@pytest.mark.parametrize('index', [0, 3, 4, 6, 8, 11, 12, 15, 18, 19, 21, 22])
async def test_fixed_http_preserves_expected_failures_and_healthy_pair(index):
    case = paired_cases()[index]
    row = await fixed_case(case)
    assert row['outcome'] == case['expected']
    if case['fault'] != 'none':
        assert (await fixed_case(case, healthy=True))['outcome'] == 'PASS'
        assert any(check[:3] == case['failed_check'] and check[3] is False
                   for check in row['checks'])
    if case['invalid_kind']:
        assert row['requests'] == 0


def test_unrelated_plan_rejection_is_not_correct_parameter_diagnosis():
    from agenttest.diagnostics import Diagnostic, Layer
    from agenttest.paired_evaluation import grade_agent
    from agenttest.report import Outcome, RunReport

    case = paired_cases()[6]
    task = paired_inputs(case)[5]
    report = RunReport(scenario_sha256='a'*64, contract_sha256='b'*64,
        environment='isolated', target_origin='http://127.0.0.1', outcome=Outcome.NEEDS_REVIEW,
        diagnostic=Diagnostic(layer=Layer.INTENT, code='INTENT_TASK_CONTRACT_PLAN_MISMATCH'))
    score = grade_agent(case, task, report, None)
    assert score['safe_rejection'] is True
    assert score['parameter_rejection'] is score['task_goal_correct'] is False
