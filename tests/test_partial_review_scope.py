"""An unapproved content assertion must not drop an independently approved action.

The G2 position of the F4 recheck showed the failure: the requirement asked for one
read plus a separate content assertion on it, the assertion had no approved rule, and
the whole read disappeared from the plan. These cases keep the assertion pending while
the approved read still runs, and keep every mutation held.
"""

import pytest
from test_mixed_planning_round3 import (
    assert_finished,
    bundle,
    case,
    declared,
    ref_of,
    run_task,
    step,
    wire,
)
from test_rw_lifecycle_preparation import project as lifecycle_project

from agenttest.product_focus import ExpectationQuestion, FocusSelection
from agenttest.request_requirements import RequestRequirement, RequiredAction
from agenttest.requirement_ledger import unresolved_response_operations

D05_TEXT = '匿名读取 page=3 的分页结果；核对分页结果的 $.items 等于 "fixture"；另匿名读取健康状态'


def focus_reply(context):
    return wire(dict(
        readPage=bundle(test_normal=True, input_requirements=[
            declared('query/page', 3, ref_of(context, 'page=3'))],
            expectation_questions=[dict(
                kind='business_rule', role=None, status_choices=[],
                source_ref=ref_of(context, 'fixture'),
                question='分页结果的 $.items 是否应等于 fixture？',
                requested_behavior='确认分页内容规则')]),
        readHealth=bundle(test_normal=True)), {
            'request_1': dict(disposition='required',
                actions=[dict(operation_id='readPage', role=None)],
                before=[], same_resource=[], response_checks=[],
                forbidden_operation_ids=[], blocked_operation_ids=[]),
            'request_2': dict(disposition='needs_review', actions=[], before=[],
                same_resource=[], response_checks=[], forbidden_operation_ids=[],
                blocked_operation_ids=[]),
            'request_3': dict(disposition='required',
                actions=[dict(operation_id='readHealth', role=None)],
                before=[], same_resource=[], response_checks=[],
                forbidden_operation_ids=[], blocked_operation_ids=[]),
        })


def design_reply(context):
    return dict(interpretation='读取分页结果与健康状态，内容核对待确认', relevant=True,
                questions=[], not_covered=[], cases=[case('分页与健康状态', [
                    step(context, 'page', 'readPage', None, [
                        ('query', 'page', 3, None, None)]),
                    step(context, 'health', 'readHealth', None)])])


@pytest.mark.asyncio
async def test_pending_content_assertion_keeps_the_independent_read(tmp_path, monkeypatch):
    done, sent, _ = await run_task(tmp_path, monkeypatch, D05_TEXT, focus_reply, design_reply)
    assert_finished(done)
    summary = done['summary']
    assert sorted(sent) == ['/health', '/pages'], summary.get('design_records')
    assert not summary.get('input_blocked_operations'), summary.get('input_blocked_operations')
    entry, = summary['response_requirements']
    assert entry['selector'] == '$.items' and entry['status'] == 'NEEDS_REVIEW'
    assert entry['blocked_operations'] == [] and entry['held_operations'] == ['readPage']
    assert not summary['complete']
    assert any(question['operation_id'] == 'readPage'
               and '$.items' in question['question'] for question in summary['questions']
               ), summary['questions']


def selection(required, pending):
    return FocusSelection(
        interpretation='核对原文分项', mode='selected', question=None,
        operation_ids=sorted({required, pending}), pending_questions=[], not_covered=[],
        requirements=[
            RequestRequirement(source_id='request_1', source_quote='读取条目',
                               disposition='required', before=[], same_resource=[],
                               actions=[
                                   RequiredAction(operation_id=required, role='author')]),
            RequestRequirement(source_id='request_2', source_quote='核对条目名称',
                               disposition='needs_review', actions=[],
                               before=[], same_resource=[]),
        ],
        expectation_questions=[ExpectationQuestion(
            operation_id=pending, question='条目名称是否应等于 x？',
            requested_behavior='确认名称规则', source_quote='核对条目名称',
            kind='business_rule')],
    )


def ledger():
    return [dict(source_id='request_2', source_quote='核对条目名称', selector='$.name',
                 operator='equals', expected='x', status='NEEDS_REVIEW', witnesses=[])]


@pytest.mark.parametrize('required, pending, blocked', [
    # The clause asks for the read on its own, so only the assertion stays pending.
    ('getItem', 'getItem', set()),
    # A create must never run while any part of its approval is unresolved.
    ('createItem', 'createItem', {'createItem'}),
    # The assertion's action is not independently required, so keep it held.
    ('getItem', 'updateItem', {'updateItem'}),
])
def test_only_an_independently_approved_read_is_released(required, pending, blocked):
    entries = ledger()
    resolved = unresolved_response_operations(entries, selection(required, pending),
                                              lifecycle_project(), [])
    assert resolved == blocked
    assert entries[0]['held_operations'] == [pending]
    assert entries[0]['blocked_operations'] == sorted(blocked)
