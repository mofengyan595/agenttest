"""Exercise the C2 candidate with real task, tool, approval and HTTP-ledger entry points."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from test_product_agent import requires_deep_agents, scripted, tool_turn
from test_product_design import settings

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.human_review import ReviewDecision
from agenttest.policies import PolicyRegistry
from agenttest.product_design import TestDesignModel as DesignModel
from agenttest.product_design_compiler import PROTOCOL, SOURCE, SWITCH
from agenttest.product_schema import encoded_literal
from agenttest.report import ModelCallStats, ReportStore
from agenttest.safety import TargetConfig
from agenttest.task_service import TaskService
from agenttest.task_store import TaskConflict, TaskStore

ROOT = Path(__file__).resolve().parents[1]
GOAL = "发布前测试新增记录的正常路径及缺少必填字段。"
OWNER = "operator"


async def fixture_project(tmp_path, monkeypatch, domain, *, open_slot=False, budget=None,
                          lifecycle=False):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    from contract_tool_reuse_poc import IDENTITIES, project
    from prepare_colleague_workspace import prepare

    if domain == "parcel":
        # Build the operator-configured contract without a listener or archive dependency.
        lab = await prepare(tmp_path / "lab", 18000, {}, observe_negative_posts=True)
        identifier = lab["projects"]["parcels"]
        db = tmp_path / "lab/workspace/project-intake.sqlite3"
        with sqlite3.connect(db) as conn:
            source = json.loads(conn.execute("SELECT payload FROM projects WHERE id=?",
                                              (identifier,)).fetchone()[0])
        raw = source["document"]
        original = build_generic_project(identifier, raw, load_openapi(raw),
                                        GenericSetup.model_validate(source["generic"]), (), {})
        values = {"name": "parcel-poc", "quantity": 2,
                  "meta": {"labels": ["green"], "options": {"tracked": True}}}
        field, code, env = "quantity", None, {}
    else:
        original = project(ROOT / "tests/fixtures/contract-tool-reuse-poc/assets.json")
        values = {"title": "asset-poc", "notes": "known"}
        field, code, env = "title", "TITLE_REQUIRED", IDENTITIES
    target = TargetConfig.model_validate(dict(original.target.model_dump(),
        business_scenario="web_contract_v2",
        **({"max_requests": budget} if budget is not None else {})))
    create_id, = target.negative_post_observations
    observer = target.negative_post_observations[create_id]
    entries = original.registry.definitions()
    if lifecycle:
        subject = asset_fixture("positions.json")["subjects"]["B04"]
        variant = next(policy for policy in entries if policy.id == subject["id"])
        keep = set(asset_fixture("oracle.json")["approved"]["B04"]["policies"])
        keep.add(variant.negative.base_policy_id)
        policies = [policy.model_copy(update={"business_scenario": "web_contract_v2"})
                    for policy in entries if policy.id in keep]
        fixture = replace(original, target=target, registry=PolicyRegistry(policies))
        return fixture, variant, observer, values, code, env
    variant = next(p for p in entries if p.operation_id == create_id and p.negative
                   and p.negative.kind == "required_missing" and p.negative.name == field)
    policies = []
    for policy in entries:
        if policy.id in (variant.id, variant.negative.base_policy_id) or (
                policy.operation_id == observer.operation_id and not policy.negative):
            rules = []
            for rule in policy.binding_rules:
                if rule.location == "body" and rule.name in values and (
                        not open_slot or rule.name != field):
                    rule = rule.model_copy(update={"literal_schema": {
                        **rule.literal_schema, "enum": [values[rule.name]]}})
                rules.append(rule)
            policies.append(policy.model_copy(update={"business_scenario": "web_contract_v2",
                                                      "binding_rules": rules}))
    fixture = replace(original, target=target, registry=PolicyRegistry(policies))
    return fixture, variant, observer, values, code, env


def asset_fixture(name):
    return json.loads((ROOT / "tests/fixtures/contract-tool-reuse-poc" / name).read_bytes())


class FocusFixture:
    """Return a canonical fixture selection while the product applies the real Focus checks."""

    def __init__(self, variant):
        self.variant = variant
        self.stats = ModelCallStats(calls=1, input_tokens=30, output_tokens=20, total_tokens=50)

    async def generate(self, context):
        policy = self.variant
        atom, = context["request_atoms"]
        op = policy.operation_id
        return json.dumps(dict(interpretation="验证已批准的新增接口", mode="selected",
            operation_ids=[op], normal_operation_ids=[op], question=None, pending_questions=[],
            not_covered=[], requested_checks=[dict(policy_id=policy.id, source_quote=atom["text"])],
            requirements=[dict(source_id=atom["source_id"], source_quote=atom["text"],
                               disposition="required", actions=[dict(operation_id=op,
                               role=policy.roles[0])], before=[], same_resource=[])]))


async def idle(svc, task):
    worker = svc._workers.get(task)
    if worker is not None:
        await asyncio.wait_for(asyncio.shield(worker), timeout=35)
    return svc.db.get(task, OWNER)


async def run_task(tmp_path, monkeypatch, domain, *, enabled=True, tool="group",
                   failure=None, open_slot=False, budget=None, approve=True):
    from test_product_tasks import response

    import agenttest.product_agent as runtime
    import agenttest.product_tasks as task_runtime
    from agenttest.step_recovery import tool_attempts

    lifecycle = failure == "lifecycle"
    project, variant, observer, values, code, env = await fixture_project(
        tmp_path, monkeypatch, domain, open_slot=open_slot, budget=budget, lifecycle=lifecycle)
    variant = next(p for p in project.registry.definitions() if p.id == variant.id)
    if lifecycle:
        from contract_tool_reuse_poc import trusted_nodes

        subject = asset_fixture("positions.json")["subjects"]["B04"]
        expectations = asset_fixture("oracle.json")["approved"]["B04"]["calls"]
        catalog = {p.id: p for p in project.registry.definitions()}
        setup, bound_check = trusted_nodes(subject, catalog, subject["proposal"])
    group_id = "negative_batch_1"
    action = (tool_turn("run_test_group", "choose", group_id=group_id) if tool == "group" else
              tool_turn("run_open_obligation", "choose", candidate_id=f"design:{variant.id}"))
    model, observed = scripted(action, tool_turn("run_test_group", "again", group_id=group_id),
                               *[("已保留已测与未测证据。", {}) for _ in range(7)])
    original = runtime.run_agent

    async def offline_agent(*args, **kwargs):
        return await original(*args, model=model, **kwargs)

    sent, design_requests = [], []

    def target(request):
        authorization = request.headers.get("Authorization")
        if variant.roles[0] is None:
            assert authorization is None
        else:
            credential = env[project.target.roles[variant.roles[0]].bearer_env]
            assert authorization == f"Bearer {credential}"
        sent.append((request.method, request.url.path, dict(request.url.params),
                     request.headers.get(observer.request_header),
                     json.loads(request.content) if request.content else None))
        if lifecycle:
            expected = expectations[len(sent) - 1]
            assert (request.method, request.url.path) == (expected["method"], expected["path"])
            return httpx.Response(expected["status"], json=expected.get("response"))
        if request.method == "GET":
            count = int(failure == "cleanup_unproven" and len(sent) >= 3)
            return httpx.Response(200, json={"count": count})
        if failure == "unknown":
            raise httpx.ReadTimeout("synthetic response lost", request=request)
        if failure == "wrong_code":
            return httpx.Response(422, json={"code": "SYNTHETIC_OTHER"})
        return httpx.Response(422, json={"code": code} if code else {})

    def design_reply(request):
        design_requests.append(json.loads(request.content))
        check = dict(id="negative", operation_id=variant.operation_id, role=variant.roles[0],
                     policy_id=variant.id, arguments=[
                         dict(location=location, name=name, value=encoded_literal(value),
                              from_step=None, select=None)
                         for location, fields in (
                             ("body", values), ("headers", {observer.request_header: "a" * 32}))
                         for name, value in fields.items()])
        if failure == "binding":
            check["arguments"][0].update(value=None, from_step="unapproved", select="$.id")
        before = setup if lifecycle else []
        if lifecycle:
            check = bound_check
        return response(dict(interpretation="验证获批负向检查", relevant=True, cases=[],
            questions=[], not_covered=[], required_cases={variant.id: dict(
                title="检查获批负向场景", before=before, check=check, after=[])}))

    original_ir = task_runtime.request_planning_ir

    def conflicting_ir(*args, **kwargs):
        ir = original_ir(*args, **kwargs)
        instance, = ir.instances
        if failure == "identity":
            instance.role = "unapproved"
        else:
            argument = next(arg for arg in instance.arguments if arg.location == "body")
            argument.value = None
            argument.from_instance = "unapproved"
        return ir

    def tool_result(call_id):
        message = next(message for entry in observed for message in entry["payload"]["messages"]
                       if message.get("tool_call_id") == call_id)
        return json.loads(message["content"])

    with monkeypatch.context() as patch:
        patch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
        patch.setattr(runtime, "run_agent", offline_agent)
        if failure in {"identity", "binding"}:
            patch.setattr(task_runtime, "request_planning_ir", conflicting_ir)
        db = TaskStore(tmp_path / "system" / "tasks.sqlite3")
        svc = TaskService({}, db, ReportStore(tmp_path / "system" / "reports"), lambda _: None,
            designer_factory=lambda _: DesignModel(settings(),
                transport=httpx.MockTransport(design_reply), profile="generic"),
            focus_factory=lambda _: FocusFixture(variant), agent_model_factory=lambda _: settings(),
            environ=dict(env, AGENTTEST_DURABLE_EXECUTION="1", AGENTTEST_HITL="1",
                         AGENTTEST_DURABLE_TARGET_EPOCH="offline-epoch"),
            model_status=lambda: "CONFIGURED_MODEL", generic_intake=True)
        patch.setattr(svc, "project", lambda _: project)
        aliases = [dict(alias=alias) for alias in project.target.roles]
        patch.setattr(svc.intake, "record", lambda _: {"generic": {"roles": aliases}})
        inventory_rows = [dict(operation_id=op.operation_id, state="READY", reason="",
                               next_step="") for op in project.contract.operations]
        patch.setattr(svc.intake, "inspect", lambda _: {
            "inventory": {"operations": inventory_rows, "total": len(inventory_rows)}})
        if enabled:
            svc.environ[SWITCH] = project.registration.id
        try:
            goal = "发布前测试更新登记记录的正常路径和评分上界。" if lifecycle else GOAL
            row = await svc.submit(OWNER, project.registration.id, goal, "request")
            task = row["id"]
            stored = svc.db.context(task, OWNER)
            assert stored.get("design_compilation_protocol") == (PROTOCOL if enabled else None)
            svc.environ.pop(SWITCH, None)  # A submitted task retains its pinned protocol.
            paused = await idle(svc, task)
            if budget is not None or failure in {"identity", "binding"}:
                assert sent == [] and svc.review_records(task, OWNER) == []
                if budget is not None:
                    assert tool_result("choose")["error"]["code"] == "BATCH_REQUEST_BUDGET"
                return paused, design_requests, sent, None
            assert paused["status"] == "INTERRUPTED", paused["summary"]
            review, = svc.review_records(task, OWNER)
            assert review["status"] == "WAITING_APPROVAL" and sent == []
            cached = db.durable_state(task, OWNER)["state"]["design"]
            if not approve:
                await svc.decide_review(task, OWNER, review["review_id"],
                                        ReviewDecision(decision="reject", version=1))
                assert not sent
                return await idle(svc, task), design_requests, sent, cached
            await svc.decide_review(task, OWNER, review["review_id"],
                                    ReviewDecision(decision="approve", version=1))
            await svc.resume(task, OWNER)
            done = await idle(svc, task)
            if failure == "unknown":
                with pytest.raises(TaskConflict, match="DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW"):
                    await svc.resume(task, OWNER)
                key = db.durable_state(task, OWNER)["state"]["tool_key"]
                assert any(a["phase"] == "DISPATCHED" for a in tool_attempts(db, task, key))
            elif failure is None and not open_slot:
                assert tool_result("again")["error"]["code"] == "GROUP_ALREADY_MEASURED"
                if enabled:
                    result = tool_result("choose")
                    assert result["design"]["compilation"]["design_calls"] == 0
                    assert result["design"]["compilation"]["preflight_passed"] is True
                    assert result["requests"] == 3
                    assert result["cases"][0]["outcome"] == "PASS"
            elif failure in {"cleanup_unproven", "wrong_code"}:
                case, = tool_result("choose")["cases"]
                assert case["outcome"] == "ASSERTION_FAILED"
                if failure == "cleanup_unproven":
                    assert [x[0] for x in sent] == ["GET", "POST", "GET", "GET"]
                    assert case["cleanup"]
                else:
                    assert len(sent) == 2
            return done, design_requests, sent, cached
        finally:
            await svc.close()


@pytest.mark.asyncio
@requires_deep_agents
@pytest.mark.parametrize(("domain", "tool"), [("parcel", "group"), ("assets", "obligation")])
async def test_c2_tool_skips_design_with_equal_plan_and_evidence(
        tmp_path, monkeypatch, domain, tool):
    baseline = await run_task(tmp_path / "model", monkeypatch, domain, enabled=False, tool=tool)
    result = await run_task(tmp_path / "compiled", monkeypatch, domain, tool=tool)
    left, old_designs, old_calls, old_cache = baseline
    right, designs, calls, cache = result
    assert len(old_designs) == 1 and not designs
    assert cache["record"]["design_compilation"]["preflight_passed"] is True
    assert cache["record"]["model_call"]["calls"] == 0
    assert cache["record"]["plan_source"] == SOURCE
    assert len(cache["design"]["cases"][0]["plan"]["steps"]) == 1
    for other in (old_calls, calls):
        assert [row[0] for row in other] == ["GET", "POST", "GET"]
        assert other[0][2] == other[2][2] and next(iter(other[0][2].values())) == other[1][3]
    assert [(x[0], x[1], x[4]) for x in old_calls] == [(x[0], x[1], x[4]) for x in calls]
    # Compare the draft apart from its correlation placeholder, which preflight replaces.
    drafts = [item["design"]["cases"][0]["plan"] for item in (old_cache, cache)]
    header, = [arg["name"] for arg in drafts[0]["steps"][0]["arguments"]
               if arg["location"] == "headers"]
    for draft in drafts:
        arguments = draft["steps"][0]["arguments"]
        argument, = [arg for arg in arguments
                     if (arg["location"], arg["name"]) == ("headers", header)]
        argument["value"] = None
        arguments.sort(key=lambda arg: (arg["location"], arg["name"]))
    assert drafts[0] == drafts[1]
    old_case, = left["summary"]["cases"]
    new_case, = right["summary"]["cases"]
    assert new_case["plan_source"] == SOURCE
    assert old_case["scenario_fingerprint"] == new_case["scenario_fingerprint"]
    assert old_case["executed_policy_ids"] == new_case["executed_policy_ids"]
    assert new_case["outcome"] == "PASS"
    assert any(row["status"] == "NO_SIDE_EFFECT_PROVEN" for row in new_case["write_effects"])
    assert left["summary"]["coverage"] == right["summary"]["coverage"]
    for field in ("complete", "task_satisfied", "uncovered_negative_checks", "untested_operations"):
        assert left["summary"][field] == right["summary"][field]


@pytest.mark.asyncio
@requires_deep_agents
@pytest.mark.parametrize("mode", [
    "open", "denied", "budget", "wrong_code", "unknown", "cleanup_unproven",
    "identity", "binding", "lifecycle",
])
async def test_c2_tool_retains_existing_gates(tmp_path, monkeypatch, mode):
    done, designs, calls, cache = await run_task(tmp_path, monkeypatch, "assets",
        failure=mode, approve=mode != "denied", open_slot=mode == "open",
        budget=2 if mode == "budget" else None)
    if mode == "open":
        assert len(designs) == 1
        assert cache["record"]["design_compilation"]["reason"] == "OPEN_BASELINE_INPUT_SLOT"
        assert len(calls) == 3
    elif mode in {"budget", "denied"}:
        assert not calls and not designs
        if mode == "denied":
            assert cache["record"]["plan_source"] == SOURCE
    elif mode == "unknown":
        assert not designs and [x[0] for x in calls] == ["GET", "POST"]
    elif mode == "lifecycle":
        assert len(designs) == 1
        assert cache["record"]["design_compilation"]["status"] == "FALLBACK"
        assert [x[0] for x in calls] == ["POST", "PATCH", "DELETE", "GET"]
        assert calls[1][4] == {"rating": 5}
        case, = done["summary"]["cases"]
        assert case["outcome"] == "PASS"
        assert any(effect["cleanup_proven"] for effect in case["write_effects"])
    elif mode in {"identity", "binding"}:
        assert not calls and not done["summary"]["cases"]
        records = [row for row in done["summary"]["design_records"]
                   if row.get("design_compilation")]
        assert records
        if mode == "identity":
            assert not designs
            assert records[0]["diagnostic"]["code"] == "POLICY_SCOPE_MISMATCH"
            assert records[0]["design_compilation"]["status"] == "REJECTED"
        else:
            assert designs
            assert records[0]["status"] == "REJECTED"
            assert records[0]["design_compilation"]["reason"] == "BASELINE_DEPENDS_ON_RESOURCE"
    else:
        assert not designs
        case, = done["summary"]["cases"]
        assert case["plan_source"] == SOURCE
        if mode == "wrong_code":
            assert case["outcome"] == "ASSERTION_FAILED"
        else:
            assert any(effect["cleanup_proven"] is False for effect in case["write_effects"])
