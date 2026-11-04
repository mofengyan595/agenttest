import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator
from test_product_design import case, project, settings
from test_product_tasks import finished, response, service

from agenttest.product_design import design_groups
from agenttest.product_focus import FocusSelection
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput


def focus_response(selection, request, *, approved_checks=()):
    from agenttest.product_focus_wire import operation_checks

    context = json.loads(json.loads(request.content)["messages"][1]["content"])
    scope = dict(mode=selection["mode"], question=selection["question"], operations={
        key: (dict(input_requirements=[], input_questions=[], requested_checks=[],
                   expectation_questions=[],
                   existing_resource=key in selection.get("source_conflicts", []),
                   test_normal=key in selection.get("normal_operation_ids", []))
              if key in selection["operation_ids"] else None) for key in context["approved_ids"]}
        if selection["mode"] == "selected" else {})
    blocked = selection.get("blocked_operation_ids", [])

    def cited(value):
        value = dict(value)
        quote = value.pop("source_quote")
        source = next(s for s in context["input_sources"] if quote in s["text"])
        return dict(**value, source_ref=source["source_id"])

    for key in ("input_requirements", "input_questions"):
        for value in selection.get(key, []):
            group = next(g for g in context["input_fields"] if all(
                g[k] == value[k] for k in ("location", "name", "path"))
                and value["operation_id"] in g["operation_ids"])
            scope["operations"][value["operation_id"]][key].append(cited(dict(
                field_ref=group["field_ref"], **{k: v for k, v in value.items()
                if k not in {"operation_id", "location", "name", "path"}})))
    for question in selection.get("expectation_questions", []):
        question = {"kind": "business_rule", "role": None, "status_choices": [], **question}
        operation = question["operation_id"]
        scope["operations"][operation]["expectation_questions"].append(cited({
            key: value for key, value in question.items() if key != "operation_id"}))
    mapping = {entry["policy_id"]: (operation, reference)
        for operation, entries in operation_checks(approved_checks).items()
        for reference, entry in entries.items()}
    for check in selection.get("requested_checks", []):
        operation, reference = mapping[check["policy_id"]]
        entry = next(e for e in approved_checks if e["policy_id"] == check["policy_id"])
        length = (dict(exact_length=check.get("exact_length")) if entry.get("kind") ==
                  "boundary_outside" and entry.get("field_constraints", {}).get("type") == "string"
                  else {})
        scope["operations"][operation]["requested_checks"].append(cited(dict(
            check_ref=reference, source_quote=check["source_quote"],
            exact_value=check.get("exact_value"), **length)))

    return response(dict(interpretation=selection["interpretation"], scope=scope,
        blocked_operations={o["operation_id"]: o["operation_id"] in blocked
                            for o in context["unavailable_operations"]}))


def test_wire_selection_is_a_closed_set_and_mode_question_branches_are_consistent():
    model = FocusModel(settings(), profile="generic")
    context = dict(approved_ids=["read", "create"], user_task="新建后读取同一资源",
                   unavailable_operations=[dict(operation_id="private")])
    body = json.loads(model.prepare_request(context))
    schema = body["response_format"]["json_schema"]["schema"]
    validator = Draft202012Validator(schema)
    valid = dict(interpretation="新建后读取同一资源", scope=dict(mode="selected",
        operations={key: dict(input_requirements=[], input_questions=[],
                             requested_checks=[], expectation_questions=[],
                  test_normal=False, existing_resource=False)
                    for key in ("create", "read")}, question=None),
        blocked_operations=dict(private=True))
    assert validator.is_valid(valid)
    decoded = json.loads(model._decode_output(json.dumps(valid), context))
    assert decoded["operation_ids"] == ["create", "read"]
    assert decoded["blocked_operation_ids"] == ["private"]
    assert "uniqueItems" not in json.dumps(schema)
    missing_pending = {**valid, "blocked_operations": {}}
    assert not validator.is_valid(missing_pending)
    with pytest.raises(ValueError, match="FLAGS_INCOMPLETE"):
        model._decode_output(json.dumps(missing_pending), context)
    for scope in (dict(mode="selected", operations=dict(read=True), question="需要补充什么？"),
                  dict(mode="needs_information", operations=dict(read=True), question="请补充资料"),
                  dict(mode="selected", operations=dict(read="true", create=False), question=None),
                  dict(mode="selected", operations=dict(unapproved=True), question=None)):
        assert not validator.is_valid({**valid, "scope": scope})
    for mode, question in (("all_ready", None), ("needs_information", "请补充接口资料。")):
        assert validator.is_valid({**valid, "scope": dict(
            mode=mode, operations={}, question=question)})
    with pytest.raises(ValueError):
        model._decode_output(json.dumps({**valid, "scope": dict(mode="selected",
            operations=[None, None], question=None)}), context)
    with pytest.raises(ValueError, match="FLAGS_INCOMPLETE"):
        model._decode_output(json.dumps({**valid, "scope": dict(mode="selected",
            operations=dict(unapproved=None),
            question=None)}), context)
    sparse = {**valid, "scope": dict(mode="selected", operations=dict(create=None, read=dict(
        input_requirements=[], input_questions=[], requested_checks=[], expectation_questions=[],
        test_normal=False, existing_resource=False)),
        question=None)}
    assert validator.is_valid(sparse)
    decoded = json.loads(model._decode_output(json.dumps(sparse), context))
    assert decoded["operation_ids"] == ["read"]


def test_duplicate_wire_operation_keys_are_rejected_instead_of_repaired():
    from agenttest.diagnostics import ValidationFailure

    content = ('{"interpretation":"检查读取操作","scope":{"mode":"selected",'
        '"operations":{"read":true,"read":true},"question":null},'
        '"blocked_operations":{},"input_requirements":[]}')
    with pytest.raises(ValidationFailure) as error:
        FocusModel(settings(), profile="generic")._decode_output(content, {})
    assert error.value.diagnostic.code == "JSON_DUPLICATE_KEY"


def test_business_question_is_owned_by_its_operation_and_operator_source():
    from agenttest.product_focus import ExpectationQuestion, validate_expectation_questions

    text = "创建任务后，通知应该在多少秒送达还没规则，请具体问我。"
    context = dict(approved_ids=["insertJob", "readJob"], user_task=text)
    model = FocusModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    question = dict(question="创建后应在多少秒内通知？", requested_behavior="通知送达时间",
                    source_ref="source_1", kind="business_rule", role=None, status_choices=[])
    bundle = dict(input_requirements=[], input_questions=[], requested_checks=[],
                  expectation_questions=[question], test_normal=False, existing_resource=False)
    wire = dict(interpretation="创建后基本检查，未知通知期限补问", scope=dict(mode="selected",
        operations=dict(insertJob=bundle, readJob=None), question=None), blocked_operations={})
    assert Draft202012Validator(schema).is_valid(wire)
    decoded = json.loads(model._decode_output(json.dumps(wire), context))
    actual = decoded["expectation_questions"][0]
    assert actual["operation_id"] == "insertJob" and actual["source_quote"] == text
    question["operation_id"] = "readJob"
    assert not Draft202012Validator(schema).is_valid(wire)
    with pytest.raises(ValueError, match="FOCUS_EXPECTATION_QUESTION_INVALID"):
        model._decode_output(json.dumps(wire), context)
    forged = ExpectationQuestion.model_validate({**actual, "source_quote": "没有提出的业务要求"})
    with pytest.raises(ValueError, match="FOCUS_EXPECTATION_QUESTION_INVALID"):
        validate_expectation_questions([forged], context, ["insertJob"])


@pytest.mark.asyncio
async def test_business_question_persists_once_and_independent_normal_request_executes(
        tmp_path, monkeypatch):
    quote = "通知应该在多少秒内送达还没有规则"
    def focus(request):
        return focus_response(dict(interpretation="创建任务，补问通知送达时间", mode="selected",
            operation_ids=["insertJob"], question=None, expectation_questions=[dict(
                operation_id="insertJob", question="创建后应在多少秒内通知？",
                requested_behavior="通知送达时间", source_quote=quote)]), request)
    def designer(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert not context["allow_new_questions"]
        pending = context["deferred_questions"]
        assert len(pending) == 1 and pending[0]["operation_id"] == "insertJob"
        return response(dict(interpretation="执行已批准创建，保留待确认通知规则", cases=[case()],
            questions=[dict(existing_question_ref=pending[0]["id"])], not_covered=[]))
    sent = []
    def target(request):
        sent.append(request.url.path)
        return httpx.Response(201, json=dict(id="new-job"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, designer)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                             profile="generic")
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="业务预期待补", document=proj.document.decode(),
                                          generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "先创建任务，" + quote + "，请具体问我。",
                               uuid4().hex)
        done = await finished(svc, row)
        assert sent == ["/jobs"] and not done["summary"]["complete"]
        assert len(done["summary"]["questions"]) == 1
        assert done["summary"]["questions"][0]["requested_behavior"] == "通知送达时间"
        assert done["summary"]["cases"][0]["outcome"] == "PASS"
        await svc.close()
        from agenttest.task_store import TaskStore

        assert len(TaskStore(tmp_path / "tasks.sqlite3").get(row["id"], "operator")[
            "summary"]["questions"]) == 1
    finally:
        await svc.close()


def test_permission_question_is_actionable_and_cannot_repeat_a_confirmed_role():
    from agenttest.product_sources import input_sources

    text = "reader读取他人的资源应返回403还是404，请具体问我。"
    context = dict(approved_ids=["readJob"], user_task=text,
                   permission_question_roles=dict(readJob=["reader"]))
    question = dict(kind="permission", role="reader", status_choices=[403, 404],
        question="reader读取他人资源应返回403还是404？", requested_behavior="跨身份资源访问",
        source_ref="source_1")
    bundle = dict(input_requirements=[], input_questions=[], requested_checks=[],
                  expectation_questions=[question], test_normal=False, existing_resource=False)
    wire = dict(interpretation="补问跨身份读取的预期状态", scope=dict(mode="selected",
        operations=dict(readJob=bundle), question=None), blocked_operations={})
    model = FocusModel(settings(), profile="generic")
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    assert Draft202012Validator(schema).is_valid(wire)
    decoded = json.loads(model._decode_output(json.dumps(wire), context))
    original = decoded["expectation_questions"][0]
    assert original["kind"] == "permission" and original["role"] == "reader"
    assert original["status_choices"] == [403, 404]
    context["previous_expectation_questions"] = [original]
    context["user_task"] = "标题已经明确，其余沿用。"
    question["source_ref"] = next(s["source_id"] for s in input_sources(context)
                                  if s["origin"] == "previous_validated")
    question["requested_behavior"] = "reader访问其他身份的资源"
    model._decode_output(json.dumps(wire), context)
    context["permission_question_roles"] = {}
    schema = json.loads(model.prepare_request(context))["response_format"]["json_schema"]["schema"]
    assert not Draft202012Validator(schema).is_valid(wire)
    with pytest.raises(ValueError, match="FOCUS_EXPECTATION_QUESTION_INVALID"):
        model._decode_output(json.dumps(wire), context)


def test_operation_owned_fields_and_checks_cannot_reference_an_unselected_interface():
    fields = [dict(operation_id=op, location="query", name=name, path=[], schema=schema)
        for op, name, schema in [("readA", "active", dict(type="boolean")),
                                 ("readB", "limit", dict(type="integer", minimum=1))]]
    context = dict(approved_ids=["readA", "readB"], user_task="检查A，active=true并测类型错误",
        input_fields=fields, approved_checks=[dict(operation_id=op, policy_id=f"negative_{op}",
            location="query", name=name, kind="type_mismatch")
            for op, name in (("readA", "active"), ("readB", "limit"))])
    model = FocusModel(settings(), profile="generic")
    body = json.loads(model.prepare_request(context))
    validator = Draft202012Validator(body["response_format"]["json_schema"]["schema"])
    bundle = dict(test_normal=False, existing_resource=False, input_requirements=[dict(
        field_ref="query/active", value=True,
        source_ref="source_1")], input_questions=[], expectation_questions=[],
        requested_checks=[dict(
            check_ref="check_1", source_ref="source_1", exact_value=None)])
    wire = dict(interpretation="检查A的有效输入和类型错误", scope=dict(mode="selected",
        operations=dict(readA=bundle, readB=None), question=None), blocked_operations={})
    assert validator.is_valid(wire)
    decoded = json.loads(model._decode_output(json.dumps(wire), context))
    assert decoded["operation_ids"] == ["readA"]
    assert decoded["input_requirements"][0]["operation_id"] == "readA"
    assert decoded["requested_checks"][0]["policy_id"] == "negative_readA"
    for field in ("readB/query/limit", "query/limit"):
        invalid = json.loads(json.dumps(wire))
        invalid["scope"]["operations"]["readA"]["input_requirements"][0]["field_ref"] = field
        assert not validator.is_valid(invalid)
        with pytest.raises(ValueError, match="FOCUS_INPUT_SLOT_INVALID"):
            model._decode_output(json.dumps(invalid), context)


def test_forty_four_identical_operations_share_format_without_losing_any_operation():
    operations = [f"read{i}" for i in range(44)]
    context = dict(approved_ids=operations, user_task="检查第17个，limit=2，并试limit=0",
        input_fields=[dict(operation_id=op, location="query", name="limit", path=[],
            schema=dict(type="integer", minimum=1, maximum=3)) for op in operations],
        approved_checks=[dict(operation_id=op, policy_id=f"negative_{op}", location="query",
            name="limit", kind="boundary_outside", invalid_value=0) for op in operations])
    model = FocusModel(settings(), profile="generic")
    body = json.loads(model.prepare_request(context))
    schema = body["response_format"]["json_schema"]["schema"]
    selected = next(s for s in schema["properties"]["scope"]["anyOf"]
                    if s["properties"]["mode"]["enum"] == ["selected"])
    options = selected["properties"]["operations"]
    assert options["required"] == operations
    assert len({json.dumps(v) for v in options["properties"].values()}) == 1
    choices = {key: None for key in operations}
    choices["read17"] = dict(input_requirements=[dict(
        field_ref="query/limit", value=2, source_ref="source_1")],
        input_questions=[], expectation_questions=[], test_normal=False, existing_resource=False,
        requested_checks=[dict(
            check_ref="check_1", source_ref="source_1", exact_value=None)])
    wire = dict(interpretation="检查第17个接口的有效值和下界外值", scope=dict(mode="selected",
        operations=choices, question=None), blocked_operations={})
    assert Draft202012Validator(schema).is_valid(wire)
    decoded = json.loads(model._decode_output(json.dumps(wire), context))
    assert decoded["operation_ids"] == ["read17"]
    assert decoded["requested_checks"][0]["policy_id"] == "negative_read17"
    wire["scope"]["operations"].pop("read0")
    assert not Draft202012Validator(schema).is_valid(wire)
    with pytest.raises(ValueError, match="FOCUS_WIRE_FLAGS_INCOMPLETE"):
        model._decode_output(json.dumps(wire), context)


def test_runtime_bindings_are_not_fillable_literals_and_existing_resource_only_holds_work():
    context = dict(user_task="创建后读取同一新资源", approved_ids=["create", "read"],
        input_fields=[dict(operation_id="read", location="path_parameters", name="id", path=[],
            schema=dict(type="string"), source_dependency=True)])
    model = FocusModel(settings(), profile="generic")
    payload = json.loads(model.prepare_request(context))
    public = json.loads(payload["messages"][1]["content"])
    assert public["input_fields"] == []
    assert public["runtime_supplied_fields"][0]["field_ref"] == "path_parameters/id"
    validator = Draft202012Validator(payload["response_format"]["json_schema"]["schema"])
    bundle = dict(input_requirements=[], input_questions=[],
                  requested_checks=[], expectation_questions=[],
                  test_normal=False, existing_resource=False)
    wire = dict(interpretation="使用前置资源读取", scope=dict(mode="selected",
        operations=dict(read=bundle, create=None), question=None), blocked_operations={})
    assert validator.is_valid(wire)
    assert not json.loads(model._decode_output(json.dumps(wire), context))["source_conflicts"]
    missing_choice = json.loads(json.dumps(wire))
    missing_choice["scope"]["operations"]["read"].pop("existing_resource")
    assert not validator.is_valid(missing_choice)
    with pytest.raises(ValueError, match="FOCUS_WIRE_SELECTION_INVALID"):
        model._decode_output(json.dumps(missing_choice), context)
    injected = json.loads(json.dumps(wire))
    injected["scope"]["operations"]["read"]["input_requirements"].append(dict(
        field_ref="path_parameters/id", value="invented-id", source_ref="source_1"))
    assert not validator.is_valid(injected)
    with pytest.raises(ValueError, match="FOCUS_RUNTIME_VALUE_NOT_LITERAL"):
        model._decode_output(json.dumps(injected), context)
    bundle["existing_resource"] = True
    assert validator.is_valid(wire)
    assert json.loads(model._decode_output(json.dumps(wire), context))["source_conflicts"] == [
        "read"]
    wire["scope"]["operations"] = dict(read=None, create=bundle)
    assert not validator.is_valid(wire)
    with pytest.raises(ValueError, match="FOCUS_SOURCE_CHOICE_INVALID"):
        model._decode_output(json.dumps(wire), context)


@pytest.mark.asyncio
async def test_focus_receives_read_dependency_provenance_without_literal_id_authority():
    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import GenericSetup, build_generic_project
    from agenttest.product_focus import select_focus

    doc = json.dumps(dict(openapi="3.0.3", info=dict(title="Reports", version="1"), paths={
        "/reports/current": {"get": dict(operationId="readHeader", responses={
            "200": dict(description="ok")})},
        "/reports/{id}/export": {"get": dict(operationId="readExport", parameters=[dict(
            name="id", **{"in": "path"}, required=True, schema=dict(type="integer"))],
            responses={"200": dict(description="ok")})}}))
    setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18998",
        isolated_confirmed=True, operations=[dict(operation_id="readHeader", alias="读取已确认报表",
            statuses=[200]), dict(operation_id="readExport", alias="导出报表", statuses=[200],
            arguments=[dict(location="path_parameters", name="id", source_operation="readHeader",
                            selector="$.id")])]))
    proj = build_generic_project("web_reports", doc, load_openapi(doc), setup, environ={})

    class Focus:
        async def generate(self, context):
            assert context["resource_dependencies"] == [dict(operation_id="readExport",
                location="path_parameters", name="id", from_operation="readHeader",
                from_method="GET", selector="$.id", roles=[None])]
            assert context["input_fields"][0]["source_dependency"]
            return FocusSelection(interpretation="先查询已确认报表，再用响应编号导出。",
                mode="selected",
                operation_ids=["readExport"], question=None, pending_questions=[], not_covered=[],
                source_conflicts=[]).model_dump_json()

    selection = await select_focus(proj, Focus(), dict(current_request="导出本轮已确认的报表。"),
                                   environ={})
    assert not selection.source_conflicts and not selection.input_requirements


@pytest.mark.asyncio
async def test_existing_source_conflict_holds_consumers_and_runs_only_independent_read(
        tmp_path, monkeypatch):
    from agenttest.generic_intake import IntakeOperation

    received = []

    def focus(request):
        return focus_response(dict(interpretation="已有资源的数据来源待核对，先检查状态",
            mode="selected", operation_ids=["readJob", "health"], question=None,
            source_conflicts=["readJob"]), request)

    def design(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert context["primary_operations"] == ["health"]
        assert [o["id"] for o in context["operations"]] == ["health"]
        return response(dict(interpretation="检查独立状态", relevant=True, cases=[dict(
            title="状态正常", kind="normal", plan=dict(name="health", steps=[dict(
                id="check", operation_id="health", role=None, policy_id="web_rule_3",
                arguments=[])]))], questions=[], not_covered=[]))

    def target(request):
        received.append(request.method + " " + request.url.path)
        return httpx.Response(200, json={})

    svc = service(tmp_path, design)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    try:
        proj, setup = project()
        doc = json.loads(proj.document)
        doc["paths"]["/health"] = {"get": dict(operationId="health",
            responses={"200": dict(description="ok")})}
        setup.operations.append(IntakeOperation(operation_id="health", alias="状态",
                                                statuses=[200]))
        saved = svc.intake.save(IntakeInput(name="已有资源不能替换为新资源",
            document=json.dumps(doc), generic=setup, confirmed=True), "operator")
        done = await finished(svc, await svc.submit("operator", saved["id"],
            "读取已有资源99，先检查状态", uuid4().hex))
        assert received == ["GET /health"]
        assert not done["summary"]["complete"]
        assert "已有资源" in done["summary"]["questions"][0]["question"]
        assert any("数据来源" in op["reason"] for op in done["summary"]["untested_operations"])
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("returned_name,expected_http", [("春季资料", 1), ("EnglishName", 0)])
async def test_real_task_guard_preserves_extracted_text_without_repairing_model_plan(
        tmp_path, monkeypatch, returned_name, expected_http):
    sent = []

    def focus(request):
        return focus_response(dict(interpretation="按指定名称创建任务", mode="selected",
            operation_ids=["insertJob"], question=None, input_requirements=[dict(
                operation_id="insertJob", location="body", name="name", path=[],
                value="春季资料", source_quote="春季资料")]), request)

    draft = case()
    draft["plan"]["steps"][0]["arguments"][0]["value"] = returned_name
    svc = service(tmp_path, lambda _: response(dict(interpretation="创建指定任务",
        cases=[draft], questions=[], not_covered=[])))
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")

    def target(request):
        sent.append(json.loads(request.content))
        return httpx.Response(201, json=dict(id="new-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="输入保留", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "创建春季资料", uuid4().hex)
        done = await finished(svc, row)
        assert len(sent) == expected_http
        if expected_http:
            assert sent[0]["name"] == "春季资料" and done["summary"]["complete"]
        else:
            assert not done["summary"]["complete"]
            assert done["summary"]["design_records"][-1]["reason"] == (
                "DESIGN_EXPLICIT_INPUT_NOT_PRESERVED")
        assert done["summary"]["context_usage"]["model_calls"] == 2
    finally:
        await svc.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value,code", [
    ("name", "CHANGED_VALUE", "FOCUS_INPUT_TEXT_CHANGED"),
    ("quantity", 3, "FOCUS_INPUT_NUMBER_CHANGED")])
async def test_changed_focus_literals_have_closed_diagnostics_and_zero_http(
        tmp_path, monkeypatch, name, value, code):
    quote = "春季资料，数量2"

    def focus(request):
        return focus_response(dict(interpretation="创建指定资料", mode="selected",
            operation_ids=["insertJob"], question=None, input_requirements=[dict(
                operation_id="insertJob", location="body", name=name, path=[], value=value,
                source_quote=quote)]), request)

    svc = service(tmp_path, lambda _: pytest.fail("Invalid scope reached test design"))
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda _: pytest.fail("Invalid scope sent HTTP")))
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="取值不一致应具体说明",
            document=proj.document.decode(), generic=setup, confirmed=True), "operator")
        done = await finished(svc, await svc.submit("operator", saved["id"],
            "创建" + quote, uuid4().hex))
        summary = done["summary"]
        diagnostic = summary["design_records"][0]["model_call"]["diagnostic"]
        assert diagnostic["code"] == code
        assert diagnostic["fields"][0] == "input_requirements.0.value"
        assert "本次没有发送业务请求" in summary["reason"]
        assert summary["context_usage"]["model_calls"] == 1
        assert summary["context_usage"]["requests"] == 0
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_requested_blocked_interface_gets_a_concrete_question_without_blocking_ready_work(
        tmp_path, monkeypatch):
    from agenttest.generic_intake import IntakeOperation

    def focus(request):
        return focus_response(dict(interpretation="先创建任务，受限配置等待身份", mode="selected",
            operation_ids=["insertJob"], blocked_operation_ids=["readPrivate"], question=None,
            pending_questions=[], not_covered=[]), request)

    received = []

    def target(request):
        received.append(request.url.path)
        assert request.url.path == "/jobs"
        return httpx.Response(201, json=dict(id="fresh-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    def design(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert [q["operation_id"] for q in context["deferred_questions"]] == ["readPrivate"]
        return response(dict(interpretation="创建已批准任务",
            cases=[case()], questions=[dict(
                existing_question_ref=context["deferred_questions"][0]["id"])], not_covered=[]))

    svc = service(tmp_path, design)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")
    try:
        proj, setup = project()
        doc = json.loads(proj.document)
        doc["components"] = dict(securitySchemes=dict(bearer=dict(type="http", scheme="bearer")))
        doc["paths"]["/private"] = {"get": dict(operationId="readPrivate", summary="读取受限配置",
            security=[dict(bearer=[])], responses={"200": dict(description="ok")})}
        setup.operations.append(IntakeOperation(operation_id="readPrivate", alias="受限配置",
                                                statuses=[200]))
        saved = svc.intake.save(IntakeInput(name="部分缺身份", document=json.dumps(doc),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "先创建，受限配置缺什么请具体问我",
                               uuid4().hex)
        done = await finished(svc, row)
        assert received == ["/jobs"] and not done["summary"]["complete"]
        assert len(done["summary"]["questions"]) == 1
        question = done["summary"]["questions"][0]
        assert question["operation_id"] == "readPrivate" and "身份" in question["question"]
        assert done["summary"]["coverage"]["operation_denominator"] == 3
    finally:
        await svc.close()


def test_selected_operation_keeps_only_its_primary_and_required_dependency_closure():
    proj, _ = project()
    groups = design_groups(proj, operation_ids=["readJob"])
    assert groups[0]["primary"] == ["readJob"]
    assert groups[0]["operations"] == ["insertJob", "readJob"]
    with pytest.raises(ValueError):
        FocusSelection(interpretation="不一致", mode="selected", operation_ids=[], question=None,
                       pending_questions=[], not_covered=[])


@pytest.mark.asyncio
async def test_input_ambiguity_holds_dependent_writes_but_runs_independent_read(tmp_path,
                                                                             monkeypatch):
    from agenttest.generic_intake import IntakeOperation

    sent, designed, selections = [], [], []
    question = dict(operation_id="insertJob", location="body", name="name", path=[],
        source_quote="春季资料任务", question="名称是春季资料，还是春季资料任务？",
        choices=["春季资料", "春季资料任务"])

    def focus(request):
        selections.append(True)
        return focus_response(dict(interpretation="先检查服务，资源名称等待澄清", mode="selected",
            operation_ids=["insertJob", "readJob", "health"], question=None,
            input_questions=[question] if len(selections) == 1 else []), request)

    def design(request):
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        designed.extend(context["primary_operations"])
        return response(dict(interpretation="检查独立健康接口", cases=[dict(title="健康检查",
            kind="normal", plan=dict(name="health", steps=[dict(id="health",
                operation_id="health", policy_id="web_rule_3", role=None, arguments=[])]))],
            questions=[], not_covered=[]))

    def target(request):
        sent.append(request.method + " " + request.url.path)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, design)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")
    try:
        proj, setup = project()
        doc = json.loads(proj.document)
        doc["paths"]["/health"] = {"get": dict(operationId="health",
            responses={"200": dict(description="ok")})}
        setup.operations.append(IntakeOperation(operation_id="health", alias="健康",
                                                statuses=[200]))
        saved = svc.intake.save(IntakeInput(name="歧义局部暂停", document=json.dumps(doc),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "建立春季资料任务，读取并检查健康",
                               uuid4().hex)
        done = await finished(svc, row)
        summary = done["summary"]
        assert sent == ["GET /health"] and designed == ["health"]
        assert summary["input_blocked_operations"] == ["insertJob", "readJob"]
        assert not summary["complete"] and summary["questions"][0]["input_choices"] == (
            question["choices"])
        assert all("歧义" in o["reason"] for o in summary["untested_operations"])
        continued = await svc.submit("operator", saved["id"], "继续未完成的检查，名称仍未选择。",
                                     uuid4().hex, parent=row["id"])
        next_result = await finished(svc, continued)
        assert sent == ["GET /health", "GET /health"]
        assert len(next_result["summary"]["pending_input_questions"]) == 1
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_semantic_focus_and_design_share_usage_and_keep_blocked_work_local(tmp_path,
                                                                            monkeypatch):
    routed, designed, sent = [], [], []

    def focus(request):
        routed.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return focus_response(dict(interpretation="先创建，通知时限等待规则", mode="selected",
            operation_ids=["insertJob"], question=None,
            expectation_questions=[dict(operation_id="insertJob", source_quote="通知期限",
                question="创建后应在多少秒内通知？", requested_behavior="通知期限")],
            not_covered=["通知时限待确认"]), request)

    def model(request):
        designed.append(json.loads(json.loads(request.content)["messages"][1]["content"]))
        return response(dict(interpretation="创建已批准任务", cases=[case()],
            questions=[dict(existing_question_ref=designed[-1]["deferred_questions"][0]["id"])],
            not_covered=[dict(operation_id="insertJob", reason="通知时限待确认",
                              source_quote="通知期限")]))

    def target(request):
        sent.append(request.method)
        return httpx.Response(201, json=dict(id="fresh-job"))

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(target))
    svc = service(tmp_path, model)
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="自然范围", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "先创建，再检查通知期限", uuid4().hex)
        done = await finished(svc, row)
        assert done["status"] == "FINISHED" and not done["summary"]["complete"]
        assert sent == ["POST"] and len(routed) == len(designed) == 1
        assert designed[0]["primary_operations"] == ["insertJob"]
        assert {o["id"] for o in designed[0]["operations"]} == {"insertJob"}
        assert done["summary"]["questions"] and done["summary"]["not_covered"]
        assert done["summary"]["context_usage"]["model_calls"] == 2
        assert done["summary"]["context_usage"]["total_tokens"] == 700
    finally:
        await svc.close()


@pytest.mark.asyncio
async def test_unidentified_focus_stops_before_design_or_business_http(tmp_path, monkeypatch):
    def focus(request):
        return focus_response(dict(interpretation="文档没有支付接口", mode="needs_information",
            operation_ids=[], question="请补充支付接口资料或说明要检查的现有操作。",
            pending_questions=[], not_covered=["支付行为没有接口依据"]), request)

    svc = service(tmp_path, lambda request: pytest.fail("Unidentified scope called design"))
    svc.focus_factory = lambda _: FocusModel(settings(), transport=httpx.MockTransport(focus),
                                            profile="generic")
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(
        lambda request: pytest.fail("Unidentified scope sent HTTP")))
    try:
        proj, setup = project()
        saved = svc.intake.save(IntakeInput(name="补充范围", document=proj.document.decode(),
            generic=setup, confirmed=True), "operator")
        row = await svc.submit("operator", saved["id"], "检查支付是否正常", uuid4().hex)
        await asyncio.gather(*list(svc._workers.values()))
        done = svc.db.get(row["id"], "operator")
        assert done["status"] == "NEEDS_REVIEW"
        assert done["summary"]["context_usage"]["requests"] == 0
        assert done["summary"]["context_usage"]["model_calls"] == 1
        assert done["summary"]["questions"]
    finally:
        await svc.close()


def test_unasked_old_operator_expectation_can_be_recovered_without_execution_authority():
    from agenttest.product_focus import ExpectationQuestion, validate_expectation_questions

    old = "报告条数最低要求还没有依据，请问我。"
    question = ExpectationQuestion(operation_id="readReports", question="报告至少应有几条？",
        requested_behavior="报告最低条数", source_quote=old)
    context = dict(user_task="名称已经明确，其余继续。", previous_requests=[old])
    validate_expectation_questions([question], context, ["readReports"])
    with pytest.raises(ValueError, match="FOCUS_EXPECTATION_QUESTION_INVALID"):
        validate_expectation_questions([question], context, ["different"])


def test_actual_previous_checks_are_published_as_current_closed_refs_without_mutating_history():
    import copy

    from test_negative_values import policy, read_project

    from agenttest.product_checks import check_index
    from agenttest.product_inputs import input_fields

    proj, _ = read_project()
    actual = policy(proj)
    context = dict(user_task="继续检查尚未覆盖的参数", approved_ids=["readReports"],
        input_fields=input_fields(proj), approved_checks=check_index(proj),
        previous_result=dict(executed_policy_ids=[actual.id, "not_in_current_registry"]))
    original = copy.deepcopy(context)
    payload = json.loads(FocusModel(settings(), profile="generic").prepare_request(context))
    public = json.loads(payload["messages"][1]["content"])
    match = next(c for c in public["approved_checks"] if c["name"] == "limit"
                 and c["kind"] == actual.negative.kind)
    assert public["previous_executed_checks"] == [dict(operation_id="readReports",
                                                       check_ref=match["check_ref"])]
    assert "executed_policy_ids" not in public["previous_result"]
    assert context == original


@pytest.mark.asyncio
@pytest.mark.parametrize("password,status", [("private-focus-password", "READY"),
    ("", "MISSING"), ("private-focus-password\n", "MISSING")])
async def test_focus_sees_controlled_identity_readiness_without_credentials(password, status):
    from agenttest.product_focus import select_focus
    from agenttest.safety import RoleCredential

    proj, _ = project()
    proj.target.roles["reviewer"] = RoleCredential(login_operation="signIn",
        username_env="AGENTTEST_TARGET_FOCUS_USER", password_env="AGENTTEST_TARGET_FOCUS_PASS")
    proj.target.roles["reader"] = RoleCredential(bearer_env="AGENTTEST_TARGET_FOCUS_BEARER")
    captured = {}

    class Focus:
        async def generate(self, context):
            captured.update(context)
            # Unknown behavior remains a real question even when credentials are prepared.
            return FocusSelection(interpretation="查询任务并保留待确认的缓存业务规则。",
                mode="selected", operation_ids=["readJob"], question=None,
                pending_questions=[], not_covered=[], expectation_questions=[dict(
                    operation_id="readJob", question="任务查询允许缓存多久？",
                    requested_behavior="查询缓存时长", source_quote="查询任务的缓存时长")]
                ).model_dump_json()

    result = await select_focus(proj, Focus(),
        dict(current_request="查询任务的缓存时长"), environ={
            "AGENTTEST_TARGET_FOCUS_USER": "private-focus-user",
            "AGENTTEST_TARGET_FOCUS_PASS": password,
            "AGENTTEST_TARGET_FOCUS_BEARER": "private-focus-bearer"})
    body = FocusModel(settings(), profile="generic").prepare_request(captured).decode()
    public = json.loads(json.loads(body)["messages"][1]["content"])
    assert public["managed_authentication"] == [dict(identity="reviewer",
        login_operation="signIn", method="login", credential_status=status,
        input_source="SERVER_CONTROLLED"), dict(identity="reader", login_operation=None,
        method="bearer", credential_status="READY", input_source="SERVER_CONTROLLED")]
    assert not any(secret in body for secret in ("private-focus-password", "private-focus-user",
        "private-focus-bearer", "AGENTTEST_TARGET_FOCUS_"))
    assert len(result.expectation_questions) == 1
