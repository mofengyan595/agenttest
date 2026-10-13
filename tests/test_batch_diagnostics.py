"""Development evidence cannot silently become a complete Eval v2 intent score."""

import json

import pytest

from agenttest.batch_demo import batch_inputs, manual_batch_draft
from agenttest.batch_diagnostics import (
    batch_plan_diagnostics,
    run_batch_diagnostics,
    verify_batch_archive,
)
from agenttest.eval_v2.models import ExpectedArgument, ExpectedStep, Rubric
from agenttest.eval_v2.scoring import score_intent
from agenttest.report import fingerprint


def test_eval_v2_still_rejects_unlabeled_upload_and_poll():
    plan = manual_batch_draft().scenario()
    # This constructed label set demonstrates an expression limitation only; it is
    # not an independent rubric and is never published as a task quality score.
    steps = []
    for step in plan.steps:
        arguments = []
        for location in ('body', 'path_parameters', 'query'):
            for name, binding in getattr(step, location).items():
                arguments.append(ExpectedArgument(location=location, name=name, **(
                    {'source': binding.from_step, 'selector': binding.select}
                    if binding.from_step else {'value': binding.value})))
        steps.append(ExpectedStep(key=step.id, operation_id=step.operation_id, role=step.role,
            policy_id=step.policy_id, arguments=arguments, goals=[step.id]))
    rubric = Rubric(steps=steps, required_goals=[s.id for s in plan.steps])
    score = score_intent(rubric, plan)
    assert not score.passed
    assert 'UNLABELED_EXECUTION_OPTION:batch' in score.diagnostics
    assert 'UNLABELED_EXECUTION_OPTION:wait' in score.diagnostics
    _, _, registry, _ = batch_inputs()
    diagnostics = batch_plan_diagnostics(plan, registry)
    upload = diagnostics['options'][2]
    poll = diagnostics['options'][3]
    assert upload['fixture_id'] == 'mini_two_rows_v1'
    assert upload['upload_sha256'] == fingerprint(plan.steps[2].upload.model_dump())
    assert poll['poll_policy']['max_attempts'] == 4
    assert poll['poll_policy']['terminal_values'] == ['SUCCEEDED', 'FAILED']
    assert any(f['expected'] == 'SUCCEEDED' for f in poll['assertions']['fields'])
    assert diagnostics['plan_evidence']['steps'][3]['selectors'] == {
        'path_parameters.batchId': '$.batchId'}


@pytest.mark.asyncio
async def test_archive_checks_reports_options_and_source_freeze(tmp_path):
    pytest.importorskip('langgraph')
    directory = tmp_path / 'batch-archive'
    result = await run_batch_diagnostics(directory)
    assert result['checks_verified']
    assert result['eval_v2_intent_score'] is None and result['review_status'] == 'UNREVIEWED'
    assert len(result['records']) == 20
    assert sum(r['runtime_outcome'] == 'PASS' for r in result['records']) == 3
    assert sum(r['runtime_outcome'] == 'ASSERTION_FAILED' for r in result['records']) == 6
    assert all(r['request_count'] == 0 for r in result['records'][9:])
    assert verify_batch_archive(directory) == result
    with pytest.raises(FileExistsError):
        await run_batch_diagnostics(directory)
    evidence = directory / 'approved-plan.json'
    saved = evidence.read_bytes()
    data = json.loads(saved)
    data['options'][3]['poll_policy']['max_attempts'] = 20
    evidence.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='integrity mismatch'):
        verify_batch_archive(directory)
    evidence.write_bytes(saved)
    # Even a freshly re-sealed development record cannot self-claim human review.
    from agenttest.eval_v2.archive import inventory

    results_path, manifest_path = directory / 'results.json', directory / 'archive.json'
    results_bytes, manifest_bytes = results_path.read_bytes(), manifest_path.read_bytes()
    changed = json.loads(results_bytes)
    changed['review_status'] = 'HUMAN_REVIEWED'
    results_path.write_text(json.dumps(changed), encoding='utf-8')
    manifest = json.loads(manifest_bytes)
    manifest['files'] = inventory(directory)
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='scope mismatch'):
        verify_batch_archive(directory)
    results_path.write_bytes(results_bytes)
    manifest_path.write_bytes(manifest_bytes)
    report = directory / 'reports' / result['records'][0]['run_id'] / 'report.json'
    report.write_bytes(report.read_bytes() + b' ')
    with pytest.raises(ValueError, match='integrity mismatch'):
        verify_batch_archive(directory)
