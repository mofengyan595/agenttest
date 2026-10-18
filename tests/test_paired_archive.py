"""Missing positions remain in the denominator; logs and snapshots cannot be silently replaced."""

import json
from types import SimpleNamespace

import pytest

from agenttest.eval_v2.archive import seal
from agenttest.eval_v2.review import require_review
from agenttest.paired_evaluation import summarize, verify_paired
from agenttest.paired_tasks import paired_cases, paired_inputs
from agenttest.report import fingerprint


def test_used_paired_task_cannot_be_promoted_to_holdout_by_renaming():
    text = paired_inputs(paired_cases()[0])[5].user_task
    suite = SimpleNamespace(cases=[SimpleNamespace(task=SimpleNamespace(user_task=text))])
    with pytest.raises(ValueError, match="overlap"):
        require_review(suite)


def test_cold_archive_keeps_all_missing_positions_and_detects_tampering(tmp_path):
    cases = paired_cases()
    rows, fixed = [], []
    for r in range(1, 4):
        for case in cases:
            rows.append({'round': r, 'case_id': case['id'], 'run_id': None,
                         'not_attempted_reason': 'UNKNOWN_USAGE_OR_TRANSPORT_INFRA_STOP'})
            fixed.append({'round': r, 'case_id': case['id'], 'outcome': case['expected'],
                'requests': 0 if case['invalid_kind'] else 1,
                'checks': [[*case['failed_check'], False]] if case['failed_check'] else
                    [['read', 'http_status', None, True]],
                'healthy': {'outcome': 'PASS', 'requests': 1} if case['failed_check'] else None})
            (tmp_path / f"fixed-{r}-{case['id']}.json").write_text(json.dumps(fixed[-1]))
    for name in ('fixed-pytest.log', 'fixed-junit.xml'):
        (tmp_path / name).write_text('synthetic test-only evidence')
    artifacts = {name: fingerprint((tmp_path / name).read_bytes().hex())
                 for name in ('fixed-pytest.log', 'fixed-junit.xml')}
    result = {**summarize(rows, fixed, 'mock_transport'), 'results': rows, 'fixed': fixed,
              'elapsed_ms': 0, 'baseline_artifacts': artifacts}
    frozen = {'cases': cases, 'rounds': 3, 'max_calls': 288, 'source': 'mock_transport'}
    for name, value in [('freeze.json', frozen), ('results.json', result)]:
        (tmp_path / name).write_text(json.dumps(value))
    (tmp_path / 'summary.md').write_text('synthetic archive')
    seal(tmp_path)
    assert verify_paired(tmp_path)['attempted_reports'] == 0
    assert result['not_attempted'] == 72
    (tmp_path / 'fixed-junit.xml').write_text('replacement')
    with pytest.raises(ValueError, match='JUnit'):
        verify_paired(tmp_path)
