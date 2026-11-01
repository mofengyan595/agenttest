"""Model semantics proposes; existing policy/schema validators still decide execution."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.goal_suite import BudgetPlanner, SuiteLedger
from agenttest.llm_planner import ModelSettings, PlannerFailure
from agenttest.product_design import (
    DesignCase,
    design_context,
    design_groups,
    propose_design,
    validate_design_case,
)
from agenttest.product_design import (
    TestDesignModel as DesignModel,
)
from agenttest.report import ReportStore


def project(negative=False):
    schema = dict(type="object", required=["name", "quantity", "details"], properties=dict(
        name=dict(type="string", minLength=1, maxLength=20),
        quantity=dict(type="integer", minimum=1, maximum=3),
        details=dict(type="object", required=["labels"], properties=dict(labels=dict(
            type="array", minItems=1, maxItems=2, items=dict(type="string", enum=["a", "b"]))))))
    document = json.dumps(dict(openapi="3.0.3", info=dict(title="New jobs API", version="1"),
        paths={"/jobs": {"post": dict(operationId="insertJob", summary="创建任务",
            requestBody=dict(required=True, content={"application/json": dict(schema=schema)}),
            responses={"201": dict(description="created", links=dict(read=dict(
                operationId="readJob", parameters=dict(jobId="$response.body#/id")))),
                "422": dict(description="invalid parameters")})},
            "/jobs/{jobId}": {"get": dict(operationId="readJob", summary="查询任务",
                parameters=[dict(name="jobId", **{"in": "path"}, required=True,
                    schema=dict(type="string"))], responses={"200": dict(description="ok")})}}))
    contract = load_openapi(document)
    suggestions = analyze_contract(contract)
    operations = [dict(operation_id=s["operation_id"], alias=s["alias"], statuses=s["statuses"],
        arguments=[{k: v for k, v in a.items() if k in {"location", "name", "value",
            "source_operation", "selector", "generated", "needs_input"}}
            for a in s["arguments"]]) for s in suggestions]
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998",
        isolated_confirmed=True, allow_mutations=True, assisted=True,
        approve_negative_tests=negative, operations=operations))
    return build_generic_project("jobs", document, contract, setup), setup


def case(policy="web_rule_1", quantity=2):
    return dict(title="创建带标签的任务", kind="normal", plan=dict(name="nested", steps=[dict(
        id="insert", operation_id="insertJob", role=None, policy_id=policy, arguments=[
            dict(location="body", name="name", value="fresh-job", from_step=None, select=None),
            dict(location="body", name="quantity", value=quantity, from_step=None, select=None),
            dict(location="body", name="details", value={"object": [{"key": "labels",
                "value": {"array": ["b"]}}]}, from_step=None, select=None)])]))


def settings():
    return ModelSettings(model="unit-model", base_url="https://unit.invalid/v1",
        api_key="unit-model-private-key", max_input_tokens=60_000,
        max_total_tokens=70_000, max_output_tokens=8192)


def test_duplicate_ordinary_setup_is_rejected_but_valid_input_variations_remain_available():
    from agenttest.product_design import TestDesign, validate_distinct_setups

    proj, _ = project(negative=True)
    proposal = TestDesign.model_validate(dict(interpretation="创建后检查两种输入", relevant=True,
        cases=[case(), case()], questions=[], not_covered=[]))
    with pytest.raises(ValueError, match="DESIGN_DUPLICATE_RESOURCE_SETUP"):
        validate_distinct_setups(proj, proposal)
    # Reordering named inputs or renaming a step does not authorize another identical setup.
    proposal.cases[1].plan.steps[0].id = "different_id"
    proposal.cases[1].plan.steps[0].arguments.reverse()
    with pytest.raises(ValueError, match="DESIGN_DUPLICATE_RESOURCE_SETUP"):
        validate_distinct_setups(proj, proposal)
    proposal.cases[1] = DesignCase.model_validate(case(quantity=3))
    validate_distinct_setups(proj, proposal)


@pytest.mark.asyncio
async def test_designer_rejects_duplicate_lifecycle_before_returning_any_executable_case():
    proj, _ = project()
    calls = []

    class DuplicateDesign:
        async def generate(self, context):
            calls.append(context["user_task"])
            return json.dumps(dict(interpretation="创建同一份新资源并检查", relevant=True,
                cases=[case(), case()], questions=[], not_covered=[]))

    with pytest.raises(ValueError, match="DESIGN_DUPLICATE_RESOURCE_SETUP"):
        await propose_design(proj, "创建一份新资源", design_groups(proj)[0], DuplicateDesign(),
                             environ={}, remaining_requests=10, inventory_total=2)
    assert calls == ["创建一份新资源"]


def test_followup_omission_can_quote_persisted_request_but_never_unselected_operation():
    from agenttest.diagnostics import ValidationFailure

    model = DesignModel(settings(), profile="generic")
    context = dict(user_task="名称明确为春季资料，其余沿用", primary_operations=["insertJob"],
                   previous_requests=["创建任务前，名称需先确认。"])
    proposal = dict(interpretation="等待确认输入名称", relevant=False, cases=[], questions=[],
        not_covered=[dict(operation_id="insertJob", reason="输入名称仍未确认",
                          source_quote="名称需先确认")])
    assert json.loads(model._decode_output(json.dumps(proposal), context))["not_covered"] == (
        ["输入名称仍未确认"])
    for change in (dict(operation_id="unselected"), dict(source_quote="不存在的旧请求")):
        changed = {**proposal, "not_covered": [{**proposal["not_covered"][0], **change}]}
        with pytest.raises(ValidationFailure) as exc:
            model._decode_output(json.dumps(changed), context)
        assert exc.value.diagnostic.code == "DESIGN_OMISSION_OUTSIDE_REQUEST"


def test_existing_question_wire_reference_is_closed_and_keeps_its_original_owner():
    from jsonschema import Draft202012Validator

    proj, _ = project()
    context = design_context(proj, "先检查可执行部分，私有接口等账号",
                             design_groups(proj)[0], 10, 3)
    pending = dict(id="known_private", operation_id="privateOutsideGroup",
        question="请补充私有接口的测试身份。", requested_behavior="配置测试身份",
        kind="clarification", role=None, status_choices=[])
    context["deferred_questions"] = [pending]
    model = DesignModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    wire = dict(interpretation="执行本组并保留已有补问", relevant=True, cases=[case()],
                questions=[dict(existing_question_ref="known_private")], not_covered=[])
    assert Draft202012Validator(schema).is_valid(wire)
    question = json.loads(model._decode_output(json.dumps(wire), context))["questions"][0]
    assert question["operation_id"] == pending["operation_id"]
    assert question["question"] == pending["question"]
    for value in (dict(existing_question_ref="invented"),
                  dict(existing_question_ref="known_private", operation_id="insertJob")):
        wire["questions"] = [value]
        assert not Draft202012Validator(schema).is_valid(wire)
        with pytest.raises(ValueError, match="DESIGN_EXISTING_QUESTION_REF_INVALID"):
            model._decode_output(json.dumps(wire), context)


@pytest.mark.asyncio
async def test_a_forged_canonical_existing_question_cannot_change_an_operation_or_rule():
    proj, _ = project()
    pending = dict(id="known_private", operation_id="privateOutsideGroup",
        question="请补充测试身份。", requested_behavior="配置测试身份",
        kind="clarification", role=None, status_choices=[])
    class ForgedReference:
        async def generate(self, context):
            changed = dict(pending, operation_id="insertJob", existing_question_ref="known_private")
            changed.pop("id")
            return json.dumps(dict(interpretation="本组检查方案", relevant=True, cases=[case()],
                                   questions=[changed], not_covered=[]))
    with pytest.raises(ValueError, match="DESIGN_EXISTING_QUESTION_REF_INVALID"):
        await propose_design(proj, "检查任务", design_groups(proj)[0], ForgedReference(),
            environ={}, remaining_requests=10, inventory_total=3, handled_questions=[pending])


def test_selected_design_questions_are_closed_references_but_default_questions_remain_available():
    from jsonschema import Draft202012Validator

    proj, _ = project()
    context = design_context(proj, "创建任务，受限配置等账号", design_groups(proj)[0], 10, 3)
    context["allow_new_questions"] = False
    pending = dict(id="private_account", operation_id="privateOutsideGroup",
        question="请补充受限配置账号。", requested_behavior="配置测试身份",
        kind="clarification", role=None, status_choices=[])
    context["deferred_questions"] = [pending]
    model = DesignModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    proposal = dict(interpretation="执行创建，保留既有缺账号问题", relevant=True, cases=[case()],
        questions=[dict(existing_question_ref="private_account")], not_covered=[])
    assert Draft202012Validator(schema).is_valid(proposal)
    decoded = json.loads(model._decode_output(json.dumps(proposal), context))
    assert decoded["questions"][0]["operation_id"] == "privateOutsideGroup"
    proposal["questions"] = [{k: v for k, v in pending.items() if k != "id"}]
    proposal["questions"][0]["operation_id"] = "insertJob"
    assert not Draft202012Validator(schema).is_valid(proposal)
    with pytest.raises(ValueError, match="DESIGN_UNSCOPED_QUESTION"):
        model._decode_output(json.dumps(proposal), context)
    context["deferred_questions"] = []
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["questions"]["maxItems"] == 0
    context["allow_new_questions"] = True
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    assert Draft202012Validator(schema).is_valid(proposal)


def test_an_empty_case_is_never_an_executable_placeholder_for_a_pending_question():
    from jsonschema import Draft202012Validator

    proj, _ = project()
    context = design_context(proj, "先执行独立部分，缺少的规则请问我",
                             design_groups(proj)[0], 10, 2)
    model = DesignModel(settings(), profile="generic")
    payload = json.loads(model.prepare_request(context))
    schema = payload["response_format"]["json_schema"]["schema"]
    proposal = dict(interpretation="先执行独立部分", relevant=True, cases=[case()],
                    questions=[], not_covered=[])
    proposal["cases"][0]["plan"]["steps"] = []
    assert not Draft202012Validator(schema).is_valid(proposal)
    with pytest.raises(ValueError, match="DESIGN_CASE_EMPTY"):
        model._decode_output(json.dumps(proposal), context)


def test_grouping_preserves_dependency_closure_without_all_contract_context():
    proj, _ = project()
    groups = design_groups(proj, size=1)
    assert groups[0]["operations"] == ["insertJob"]
    assert groups[1]["operations"] == ["insertJob", "readJob"]
    context = design_context(proj, "帮我看看新建和查询是否正常", groups[1], 10, 2)
    assert len(context["operations"]) == 2
    assert "创建任务" in json.dumps(context, ensure_ascii=False)
    assert "unit-model-private-key" not in json.dumps(context)
    request = DesignModel(settings(), profile="generic").prepare_request(context)
    assert len(request) < 60_000


def test_negative_preparation_keeps_consumed_resources_and_same_case_cleanup(tmp_path):
    from test_colleague_targets import targets
    from test_project_inventory import proposed_setup

    from agenttest.product_design import TestDesign, validate_negative_preparation
    from agenttest.product_schema import encoded_literal

    raw = json.dumps(targets.document("parcels"))
    contract = load_openapi(raw)
    setup = proposed_setup(contract)
    setup.allow_mutations = setup.approve_negative_tests = True
    proj = build_generic_project("parcels", raw, contract, setup)
    rules = proj.registry.definitions()
    check = next(p for p in rules if p.negative and p.operation_id == "changeParcel"
                 and p.negative.kind == "type_mismatch")
    def draft(operation, identifier, policy=None):
        configured = next(o for o in setup.operations if o.operation_id == operation)
        policy = policy or next(p.id for p in rules
                                if p.operation_id == operation and not p.negative)
        return dict(id=identifier, operation_id=operation, role=None, policy_id=policy, arguments=[
            dict(location=a.location, name=a.name, value=None if a.source_operation
                 else encoded_literal(a.value), from_step="prepare" if a.source_operation else None,
                 select=a.selector if a.source_operation else None) for a in configured.arguments])
    prepare = draft("createParcel", "prepare")
    invalid = draft("changeParcel", "invalid", check.id)
    cleanup = draft("deleteParcel", "cleanup")
    case = DesignCase.model_validate(dict(title="准备资源后验证修改错误并清理", kind="negative",
        plan=dict(name="negative-update", steps=[prepare, invalid, cleanup])))
    design = TestDesign(interpretation="检查修改输入并清理本轮数据", relevant=True,
                        cases=[case], questions=[], not_covered=[])
    validate_negative_preparation(proj, design)
    validate_design_case(proj, case, ReportStore(tmp_path), environ={},
                         cleanup_operations=["deleteParcel"])
    context = design_context(proj, "检查修改名称类型错误", design_groups(proj)[0], 40, 6)
    context["requested_checks"] = [dict(policy_id=check.id, source_quote="名称类型错误")]
    model = DesignModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    from jsonschema import Draft202012Validator

    wire = dict(interpretation="检查修改错误并清理数据", relevant=True, cases=[], questions=[],
        not_covered=[], required_cases={check.id: dict(title=case.title, before=[prepare],
                                                     check=invalid, after=[cleanup])})
    assert Draft202012Validator(schema).is_valid(wire)
    for replacement in ([prepare, draft("createParcel", "unneeded"), invalid, cleanup],
                        [prepare, cleanup, invalid]):
        design.cases[0].plan.steps = DesignCase.model_validate(dict(title=case.title,
            kind="negative", plan=dict(name="extra", steps=replacement))).plan.steps
        with pytest.raises(ValueError, match="DESIGN_NEGATIVE_UNNEEDED_WRITE"):
            validate_negative_preparation(proj, design)


def test_selected_cleanup_rejects_another_cases_resource_and_preserves_create_only_scope():
    from dataclasses import replace

    from agenttest.dsl import ArgumentBinding, Scenario, Step
    from agenttest.product_design import validate_cleanup_dependencies

    proj, _ = project()
    operations = [op.model_copy(update={"method": "DELETE"})
                  if op.operation_id == "readJob" else op for op in proj.contract.operations]
    proj = replace(proj, contract=proj.contract.model_copy(update={"operations": operations}))
    creation = DesignCase.model_validate(case()).plan.scenario().steps[0]
    removal = Step(id="cleanup", operation_id="readJob", policy_id="web_rule_2",
        path_parameters=dict(jobId=ArgumentBinding(from_step="insert", select="$.id")))
    plan = Scenario(name="one-resource", steps=[creation, removal])
    validate_cleanup_dependencies(proj, plan, ["readJob"])
    plan.steps = [creation]
    validate_cleanup_dependencies(proj, plan, [])
    with pytest.raises(ValueError, match="SELECTED_CLEANUP_MISSING"):
        validate_cleanup_dependencies(proj, plan, ["readJob"])
    other = creation.model_copy(update={"id": "another"})
    removal.path_parameters["jobId"] = ArgumentBinding(from_step="another", select="$.id")
    plan.steps = [creation, other, removal]
    with pytest.raises(ValueError, match="SELECTED_CLEANUP_MISSING"):
        validate_cleanup_dependencies(proj, plan, ["readJob"])


@pytest.mark.asyncio
async def test_outside_focus_group_is_explicit_and_cannot_hide_proposed_work():
    from agenttest.product_design import TestDesign

    proj, _ = project()
    design = TestDesign(interpretation="此分组不属于指定的读取重点。", relevant=False,
                        cases=[], questions=[], not_covered=[])

    class Reply:
        async def generate(self, context):
            return design.model_dump_json()

    result = await propose_design(proj, "只检查登录", design_groups(proj)[0], Reply(),
        environ={}, remaining_requests=10, inventory_total=2)
    assert not result.relevant and not result.not_covered
    design.cases = [DesignCase.model_validate(case())]
    with pytest.raises(ValueError, match="IRRELEVANT_DESIGN_HAS_REQUESTED_WORK"):
        await propose_design(proj, "只检查登录", design_groups(proj)[0], Reply(),
            environ={}, remaining_requests=10, inventory_total=2)


@pytest.mark.asyncio
async def test_designer_uses_usage_accounted_transport_and_retains_unknown_expectation(tmp_path):
    proj, _ = project()
    response = dict(interpretation="检查新建；通知期限还需要业务规则", cases=[case()],
        questions=[dict(operation_id="insertJob", question="新建后应在多少秒内通知？",
                        requested_behavior="通知期限", kind="clarification", role=None,
                        status_choices=[])], not_covered=[dict(operation_id="insertJob",
                            reason="通知期限没有已确认规则", source_quote="通知期限")])
    observed = []

    def model(request):
        observed.append(json.loads(request.content))
        return httpx.Response(200, json=dict(usage=dict(prompt_tokens=100, completion_tokens=50,
            total_tokens=150), choices=[dict(finish_reason="stop", message=dict(
                content=json.dumps(response, ensure_ascii=False)))]))

    raw = DesignModel(settings(), transport=httpx.MockTransport(model), profile="generic")
    ledger = SuiteLedger(10, 2, 150_000)
    design = await propose_design(proj, "帮我检查新建任务并核验通知期限", design_groups(proj)[0],
        BudgetPlanner(raw, ledger), environ={}, remaining_requests=10, inventory_total=2)
    assert len(observed) == 1 and ledger.model_calls == 1 and ledger.known_tokens == 150
    assert design.questions[0].requested_behavior == "通知期限"
    scenario = validate_design_case(proj, design.cases[0], ReportStore(tmp_path), environ={})
    assert scenario.steps[0].body["details"].value == {"labels": ["b"]}
    with pytest.raises(ValueError):
        validate_design_case(proj, DesignCase.model_validate(case(quantity=20)),
                             ReportStore(tmp_path), environ={})
    with pytest.raises(ValueError):
        validate_design_case(proj, DesignCase.model_validate(case(policy="invented")),
                             ReportStore(tmp_path), environ={})


def test_negative_tests_use_fixed_capabilities_and_keep_global_request_validation(tmp_path):
    proj, _ = project(negative=True)
    negatives = [p for p in proj.registry.definitions() if p.negative]
    assert {p.negative.kind for p in negatives} >= {"required_missing", "type_mismatch",
                                                  "boundary_outside"}
    policy = next(p for p in negatives if p.negative.name == "quantity"
                  and p.negative.kind == "boundary_outside")
    scenario = validate_design_case(proj, DesignCase.model_validate(case(policy=policy.id)),
                                    ReportStore(tmp_path), environ={})
    assert scenario.steps[0].body["quantity"].value == 2
    assert policy.assertions.status.codes == [422]
    # A planner still cannot submit invalid values itself, even under a negative policy.
    with pytest.raises(ValueError):
        validate_design_case(proj, DesignCase.model_validate(case(policy=policy.id, quantity=100)),
                             ReportStore(tmp_path), environ={})


@pytest.mark.asyncio
async def test_unknown_model_usage_stops_the_next_call():
    proj, _ = project()
    calls = []

    def model(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": []})

    ledger = SuiteLedger(10, 2, 150_000)
    bounded = BudgetPlanner(DesignModel(settings(), transport=httpx.MockTransport(model),
                                            profile="generic"), ledger)
    context = design_context(proj, "开始一轮测试", design_groups(proj)[0], 10, 2)
    with pytest.raises(PlannerFailure):
        await bounded.generate(context)
    assert ledger.usage_unknown
    with pytest.raises(PlannerFailure, match="SUITE_MODEL_BUDGET_EXHAUSTED"):
        await bounded.generate(context)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_negative_traffic_never_satisfies_a_required_normal_request():
    from test_negative_values import case as read_case
    from test_negative_values import policy, read_project

    proj, _ = read_project()
    negative = read_case(policy(proj).id)
    negative["kind"] = "negative"
    class NegativeOnly:
        async def generate(self, context):
            assert context["required_normal_operations"] == ["readReports"]
            return json.dumps(dict(interpretation="仅安排已批准的类型拒绝检查", relevant=True,
                cases=[negative], questions=[], not_covered=[]))
    with pytest.raises(ValueError, match="DESIGN_NORMAL_REQUEST_MISSING"):
        await propose_design(proj, "正常读取，再检查错误参数", design_groups(proj)[0],
            NegativeOnly(), environ={}, remaining_requests=10, inventory_total=1,
            normal_operation_ids=["readReports"])
    class Both:
        async def generate(self, context):
            normal = next(p for p in proj.registry.definitions() if not p.negative)
            return json.dumps(dict(interpretation="正常读取与异常拒绝分别检查", relevant=True,
                cases=[read_case(normal.id), negative], questions=[], not_covered=[]))
    result = await propose_design(proj, "正常读取，再检查错误参数", design_groups(proj)[0],
        Both(), environ={}, remaining_requests=10, inventory_total=1,
        normal_operation_ids=["readReports"])
    assert len(result.cases) == 2


def test_identical_ordinary_reads_cannot_create_new_scenarios_by_renaming_steps():
    import copy

    from test_negative_values import case as read_case
    from test_negative_values import read_project

    from agenttest.product_design import TestDesign, validate_distinct_setups

    proj, _ = read_project()
    base = next(p for p in proj.registry.definitions() if not p.negative)
    first = read_case(base.id)
    repeated = copy.deepcopy(first)
    repeated["title"] = "换一个标题仍是相同请求"
    repeated["plan"]["steps"][0]["id"] = "renamed"
    design = TestDesign.model_validate(dict(interpretation="两个名称不能产生额外覆盖",
        cases=[first, repeated], questions=[], not_covered=[]))
    with pytest.raises(ValueError, match="DESIGN_DUPLICATE_READ_SCENARIO"):
        validate_distinct_setups(proj, design)
    repeated["plan"]["steps"][0]["arguments"][0]["value"] = 2
    varied = TestDesign.model_validate(dict(interpretation="不同合法输入是不同场景",
        cases=[first, repeated], questions=[], not_covered=[]))
    validate_distinct_setups(proj, varied)


def test_a_negative_category_without_a_negative_policy_stops_before_execution():
    from agenttest.product_design import TestDesign, validate_negative_preparation

    proj, _ = project()
    ordinary = case()
    ordinary["kind"] = "negative"
    design = TestDesign.model_validate(dict(interpretation="名为异常但实际没有异常策略",
        cases=[ordinary], questions=[], not_covered=[]))
    with pytest.raises(ValueError, match="DESIGN_NEGATIVE_CHECK_MISSING"):
        validate_negative_preparation(proj, design)


def test_omission_wire_uses_closed_operator_sources_without_retyping_or_forgery():
    from jsonschema import Draft202012Validator

    from agenttest.diagnostics import ValidationFailure

    proj, _ = project()
    text = "先创建资料；通知时限尚无业务依据，请具体询问。"
    context = design_context(proj, text, design_groups(proj)[0], 20, 2)
    model = DesignModel(settings(), profile="generic")
    body = json.loads(model.prepare_request(context))
    public = json.loads(body["messages"][1]["content"])
    assert public["omission_sources"][0] == dict(request_ref="request_1", text=text)
    validator = Draft202012Validator(body["response_format"]["json_schema"]["schema"])
    wire = dict(interpretation="先执行有依据的创建，保留未知通知时限", relevant=True,
        cases=[case()], questions=[], not_covered=[dict(operation_id="insertJob",
            reason="通知时限仍待确认", request_ref="request_1")])
    assert validator.is_valid(wire)
    assert json.loads(model._decode_output(json.dumps(wire), context))["not_covered"] == [
        "通知时限仍待确认"]
    for field, value in [("request_ref", "invented"), ("operation_id", "unselected")]:
        changed = json.loads(json.dumps(wire))
        changed["not_covered"][0][field] = value
        assert not validator.is_valid(changed)
        with pytest.raises(ValidationFailure) as error:
            model._decode_output(json.dumps(changed), context)
        assert error.value.diagnostic.code == "DESIGN_OMISSION_OUTSIDE_REQUEST"
        assert error.value.diagnostic.fields == [f"not_covered.0.{field}"]
