import json
from uuid import uuid4

import httpx
import pytest
from test_product_design import case, project, settings
from test_product_focus import focus_response
from test_product_tasks import finished, response, service

from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["selected", "all_ready"])
async def test_focused_task_does_not_offer_unrequested_negative_writes(tmp_path, monkeypatch, mode):
    observed, requests = [], []
    flow = case()
    flow["plan"]["steps"].append(dict(id="read", operation_id="readJob", role=None,
        policy_id="web_rule_2", arguments=[dict(location="path_parameters", name="jobId",
            value=None, from_step="insert", select="$.id")]))

    def design(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        observed.append(context)
        return response(dict(interpretation="创建后读取本轮新任务", cases=[flow],
                             questions=[], not_covered=[]))

    def focus(request):
        return focus_response(dict(interpretation="按用户交代选择测试范围", mode=mode,
            operation_ids=["insertJob", "readJob"] if mode == "selected" else [],
            normal_operation_ids=["insertJob", "readJob"] if mode == "selected" else [],
            question=None), request)

    def target(request):
        requests.append(request.method)
        return httpx.Response(201 if request.method == "POST" else 200, json=dict(id="scope-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, design)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile="generic")
    try:
        proj, setup = project(negative=True)
        saved = svc.intake.save(IntakeInput(name="测试范围", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        done = await finished(svc, await svc.submit("operator", saved["id"],
            "创建任务并读回" if mode == "selected" else "安排项目全部就绪接口", uuid4().hex))
        assert done["status"] == "FINISHED" and done["summary"]["complete"], done["summary"]
        assert requests == ["POST", "GET"]
        assert bool(observed[0]["approved_negative_expectations"]) == (mode == "all_ready")
        assert any(p.get("negative_test") for p in observed[0]["policies"]) == (mode == "all_ready")
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_independent_scope_validator_rejects_forged_extra_negative_policy():
    from agenttest.product_design import design_groups, propose_design

    proj, _ = project(negative=True)
    negative = next(p for p in proj.registry.definitions() if p.negative)
    extra = case(negative.id)
    extra["kind"] = "negative"

    class Forged:
        async def generate(self, context):
            assert all(not p.get("negative_test") for p in context["policies"])
            return json.dumps(dict(interpretation="试图添加本次没有选择的参数检查", relevant=True,
                cases=[extra], questions=[], not_covered=[]))

    with pytest.raises(ValueError, match="DESIGN_NEGATIVE_CHECK_NOT_REQUESTED"):
        await propose_design(proj, "仅创建正常任务", design_groups(proj)[0], Forged(),
            environ={}, remaining_requests=40, inventory_total=2, allow_optional_negatives=False)
