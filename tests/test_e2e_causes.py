"""Historical failure shapes remain rejected, rather than silently repaired."""

import pytest

pytest.importorskip("langgraph")

from agenttest.contracts import load_openapi
from agenttest.eval_v2.scoring import score_intent
from agenttest.evidence_tasks import evidence_cases, evidence_inputs
from agenttest.policies import NeedsReview
from agenttest.task_contract import task_validator


@pytest.mark.parametrize("index,cause", [(0, "ORDER_MISMATCH:decoy:create"),
                                         (1, "OPERATION_ROLE_POLICY_MISMATCH")])
def test_old_failure_shapes_match_original_requirements(index, cause):
    _, doc, target, policies, _, task, draft, _ = evidence_inputs(
        evidence_cases("end_to_end")[index])
    plan = draft.scenario()
    if index == 0:
        decoy = next(s for s in plan.steps if s.id == "decoy")
        plan.steps.remove(decoy)
        plan.steps.insert(-1, decoy)
    else:
        next(s for s in plan.steps if s.policy_id == "ticket_closed_read_v1").policy_id = \
            "ticket_read_v1"
    score = score_intent(task.requirements, plan)
    assert any(d.startswith(cause) for d in score.diagnostics), score.diagnostics
    original = plan.model_dump_json()
    with pytest.raises(NeedsReview, match="INTENT_TASK_CONTRACT_PLAN_MISMATCH") as rejected:
        task_validator(task, load_openapi(doc), target, policies)(task.user_task, plan)
    assert plan.model_dump_json() == original
    details = rejected.value.diagnostic.intent
    assert details and details[0].branch == ("ORDER" if index == 0 else "OPERATION_ROLE_POLICY")
    assert "前置资源" not in rejected.value.diagnostic.model_dump_json()
