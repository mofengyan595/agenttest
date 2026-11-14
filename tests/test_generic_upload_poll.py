"""Generic web options use the existing CSV executor and bounded poll verifier."""

import json

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
from agenttest.product_design import DesignCase, validate_design_case
from agenttest.project_inventory import inventory
from agenttest.report import Outcome, ReportStore


def document():
    return json.dumps(dict(openapi="3.0.3", info=dict(title="Imports", version="1"), paths={
        "/imports": dict(post=dict(operationId="sendImport", requestBody=dict(required=True,
            content={"multipart/form-data": dict(schema=dict(type="object", required=["sheet"],
                properties=dict(sheet=dict(type="string", format="binary"))))}),
            responses={"201": dict(description="created")})),
        "/imports/{job}": dict(get=dict(operationId="readImport", parameters=[dict(
            name="job", **{"in": "path"}, required=True, schema=dict(type="string"))],
            responses={"200": dict(description="progress")}))}))


def setup():
    return GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18999",
        isolated_confirmed=True, allow_mutations=True, assisted=True, operations=[dict(
            operation_id="sendImport", alias="上传导入", statuses=[201],
            upload=dict(field="sheet", filename="records.csv", content="text\nsynthetic\n")),
            dict(operation_id="readImport", alias="等待导入", statuses=[200], arguments=[dict(
                location="path_parameters", name="job", source_operation="sendImport",
                selector="$.job")], checks=[dict(select="$.state", expected="DONE")],
                poll=dict(select="$.state", terminal_values=["DONE", "FAILED"],
                          progress_values=["PENDING"], max_attempts=3, interval=0.0))]))


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal,expected", [("DONE", Outcome.PASS),
                                               ("FAILED", Outcome.ASSERTION_FAILED)])
async def test_web_options_materialize_before_preflight_and_verify_terminal(tmp_path,
                                                                          terminal, expected):
    raw, config = document(), setup()
    contract = load_openapi(raw)
    assert all(o["supported"] for o in analyze_contract(contract))
    project = build_generic_project("web_imports", raw, contract, config, environ={})
    policies = {p.operation_id: p.id for p in project.registry.definitions()}
    case = DesignCase.model_validate(dict(title="导入并核验完成", kind="flow", plan=dict(
        name="Import", steps=[dict(id="send", operation_id="sendImport", role=None,
            policy_id=policies["sendImport"], arguments=[]), dict(id="wait",
            operation_id="readImport", role=None, policy_id=policies["readImport"],
            arguments=[dict(location="path_parameters", name="job", value=None,
                            from_step="send", select="$.job")])])))
    calls = []

    def target(request):
        calls.append(request.method)
        if request.method == "POST":
            assert b'name="sheet"' in request.content and b"text\nsynthetic\n" in request.content
            return httpx.Response(201, json=dict(job="fresh_01"))
        assert request.url.path == "/imports/fresh_01"
        return httpx.Response(200, json=dict(state="PENDING" if len(calls) == 2 else terminal))

    transport = httpx.MockTransport(target)
    store = ReportStore(tmp_path)
    scenario = validate_design_case(project, case, store, environ={}, transport=transport)
    assert scenario.steps[0].upload == config.operations[0].upload
    assert scenario.steps[1].poll
    report = await Executor(project.target, contract, store, transport, {}, project.registry).run(
        scenario)
    assert report.outcome == expected
    assert calls == ["POST", "GET", "GET"]
    assert len(report.steps[1].attempts) == 2
    assert "synthetic" not in report.model_dump_json()


def test_upload_missing_is_pending_and_poll_needs_success_assertion():
    raw, config = document(), setup()
    config.operations[0].upload = None
    entries = inventory(load_openapi(raw), config, {})["operations"]
    assert entries[0]["state"] == entries[1]["state"] == "NEEDS_INPUT"
    config = setup()
    config.operations[1].checks = []
    with pytest.raises(ValueError, match="POLL_TERMINAL_ASSERTION_REQUIRED"):
        build_generic_project("web_imports", raw, load_openapi(raw), config, environ={})


def test_focus_indexes_multipart_resource_dependencies_without_file_contents():
    from agenttest.product_inputs import input_fields

    raw = json.loads(document())
    raw["paths"]["/imports"] = {"post": dict(operationId="createFolder", responses={
        "201": dict(description="created")})}
    raw["paths"]["/imports/{folder}/files"] = {"post": dict(operationId="sendImport",
        parameters=[dict(name="folder", **{"in": "path"}, required=True,
                         schema=dict(type="string"))],
        requestBody=dict(required=True, content={"multipart/form-data": dict(schema=dict(
            type="object", required=["sheet"], properties=dict(sheet=dict(
                type="string", format="binary"))))}),
        responses={"201": dict(description="created")})}
    raw = json.dumps(raw)
    config = setup()
    from agenttest.generic_intake import IntakeArgument, IntakeOperation

    config.operations.insert(0, IntakeOperation(operation_id="createFolder", alias="创建本轮目录",
                                               statuses=[201]))

    config.operations[1].arguments = [IntakeArgument(location="path_parameters", name="folder",
        source_operation="createFolder", selector="$.id")]
    project = build_generic_project("web_imports", raw, load_openapi(raw), config, environ={})
    fields = input_fields(project)
    assert {f["name"] for f in fields} == {"folder", "job"}
    assert all(f["source_dependency"] for f in fields)
    assert "synthetic" not in json.dumps(fields)


def test_reviewed_wait_limits_reach_executor_without_changing_legacy_defaults():
    raw, config = document(), setup()
    assert "run_timeout" not in config.model_dump()
    assert "read_timeout" not in config.model_dump()
    config.run_timeout, config.read_timeout = 120.0, 15.0
    project = build_generic_project("web_imports", raw, load_openapi(raw), config, environ={})
    assert project.target.run_timeout == 120.0 and project.target.read_timeout == 15.0
    for changes in (dict(run_timeout=3_601.0), dict(read_timeout=31.0)):
        with pytest.raises(ValueError):
            GenericSetup.model_validate({**config.model_dump(), **changes})
    # A long autonomous round is a formal setting now, not a source constant: 301s is valid
    # while the absolute ceiling still refuses anything beyond an hour.
    long_round = GenericSetup.model_validate({**config.model_dump(), "run_timeout": 301.0})
    assert long_round.run_timeout == 301.0
