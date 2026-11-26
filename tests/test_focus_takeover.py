"""Exercise the compiled Focus protocol from the product task entry without business HTTP."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_focus_compiler import HOLDOUT
from test_focus_compiler import project as frozen_project_fixture
from test_product_agent import requires_deep_agents, scripted, tool_turn
from test_product_design import project, settings
from test_product_tasks import finished, response, service

from agenttest.generic_intake import GenericSetup, analyze_contract
from agenttest.policies import PolicyRegistry, RegisteredPolicy
from agenttest.product_checks import check_index
from agenttest.product_focus import TestFocusModel as FocusModel
from agenttest.project_intake import IntakeInput
from agenttest.request_validation import request_schemas

observatory_project = frozen_project_fixture
FLAG = "AGENTTEST_FOCUS_COMPILED_RELEASE_PROJECT"
CLOSED = HOLDOUT["cases"][0]["text"]
ROLE = {"queryReadings": "observer", "readLedger": "auditor", "readPulse": None,
        "readGauge": "observer"}
FROZEN_VALID_CASES = {case["id"]: case for case in HOLDOUT["cases"]}


def transform(project, change):
    policies = project.registry.definitions()
    contract, target = project.contract, project.target
    if change == "reverse":
        policies.reverse()
    elif change == "renamed":
        contract = contract.model_copy(update={"operations": [
            op.model_copy(update={"operation_id": "readGauge"})
            if op.operation_id == "queryReadings" else op for op in contract.operations]})
        target = target.model_copy(update={"allowed_operations": [
            "readGauge" if op == "queryReadings" else op for op in target.allowed_operations]})
        policies = [p.model_copy(update={"operation_id": "readGauge",
                    "id": "revised_limit" if p.id == "reading_limit" else p.id})
                    if p.operation_id == "queryReadings" else p for p in policies]
    elif change == "add":
        base = next(p for p in policies if p.id == "ledger_limit").model_dump()
        base.update(id="ledger_missing", negative=dict(base["negative"],
            kind="required_missing", value=None, violation_codes=["OPENAPI_PARAMETER_REQUIRED"],
            allow_value_variants=False))
        policies.append(RegisteredPolicy.model_validate(base))
    elif change == "remove":
        policies = [p for p in policies if p.id != "ledger_limit"]
    elif change == "zero":
        policies = [p for p in policies if p.negative is None]
    return replace(project, registry=PolicyRegistry(policies), contract=contract, target=target)


def setup_for(project):
    operations = [dict(operation_id=row["operation_id"], alias=row["alias"],
        statuses=row["statuses"], role=ROLE[row["operation_id"]],
        arguments=[{key: value for key, value in arg.items()
                    if key in {"location", "name", "value", "generated", "needs_input"}}
                   for arg in row["arguments"]]) for row in analyze_contract(project.contract)]
    return GenericSetup.model_validate(dict(base_url=project.target.base_url,
        isolated_confirmed=True, allow_mutations=True, assisted=True, operations=operations,
        roles=[dict(alias=role, bearer_env="AGENTTEST_TARGET_" + spec.bearer_env)
               for role, spec in project.target.roles.items()]))


def product_policies(project, scenario):
    operations = {op.operation_id: op for op in project.contract.operations}
    policies = []
    for policy in project.registry.definitions():
        operation = operations[policy.operation_id]
        parameters, body = request_schemas(operation,
            "application/json" if operation.request_body else None)
        fields = {"body": (body or {}).get("properties", {}), "query": parameters["query"]}
        # The shadow fixture needs only binding slots. The actual Focus codec needs their types.
        bindings = [rule.model_copy(update={"literal_schema": fields[rule.location][rule.name]})
                    for rule in policy.binding_rules]
        policies.append(policy.model_copy(update={"business_scenario": scenario,
                                                   "binding_rules": bindings}))
    return PolicyRegistry(policies)


def scope_reply(context, catalogue):
    compiled = "focus_check_protocol" in context
    bundles = {op: dict(input_requirements=[], input_questions=[], expectation_questions=[],
                        test_normal=True, existing_resource=False)
               for op in context["approved_ids"]}
    neg_ref = next(source["source_id"] for source in context["input_sources"]
                   if source["text"] == "正常路径和非法入参都按已批准的规则执行")
    if not compiled:
        for op, bundle in bundles.items():
            bundle["requested_checks"] = []
            for number, entry in enumerate(
                    (row for row in catalogue if row["operation_id"] == op), 1):
                check = dict(check_ref=f"check_{number}", source_ref=neg_ref, exact_value=None)
                if (entry["kind"] == "boundary_outside"
                        and entry["field_constraints"]["type"] == "string"):
                    check["exact_length"] = None
                bundle["requested_checks"].append(check)
    actions = [dict(operation_id=op, role=ROLE.get(op)) for op in context["approved_ids"]]
    links = [dict(producer=row["from_operation"], consumer=row["operation_id"])
             for row in context["resource_dependencies"]
             if {row["from_operation"], row["operation_id"]} <= set(bundles)]
    return dict(interpretation="按已批准的规则验收整个项目", scope=dict(mode="selected",
        operations=bundles, question=None), blocked_operations={
            row["operation_id"]: False for row in context["unavailable_operations"]},
        requirement_map=dict(request_1=dict(disposition="required", actions=actions,
            before=[], same_resource=links, response_checks=[],
            forbidden_operation_ids=[], blocked_operation_ids=[])))


def model_schema(body):
    return body["response_format"]["json_schema"]["schema"]


@pytest.fixture
def no_business(monkeypatch):
    attempts = []

    def reject(request):
        attempts.append(request.method)
        raise AssertionError("Business HTTP forbidden in offline Focus takeover")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(reject))
    yield attempts
    assert attempts == []


async def run_task(tmp_path, built, setup, *, enabled, task=CLOSED, projection=False,
                   change_reply=None, invalid=False, missing=None, autonomy=False,
                   wait_review=False, follow_up=None, no_transport=None, monkeypatch=None):
    focus_requests, design_requests, proposals, agent_requests = [], [], [], []

    def design(request):
        design_requests.append(json.loads(request.content))
        return response({})  # The real Design validator stops this before preflight/dispatch.

    svc = service(tmp_path, design)
    try:
        if autonomy:
            import agenttest.product_agent as runtime

            original = runtime.run_agent
            group_id = ("negative_batch_1" if any(p.negative for p in built.registry.definitions())
                        else "group_1")
            actions = [tool_turn("run_test_group", "design", group_id=group_id)]
            model, agent_requests = scripted(*actions,
                *[("记录离线设计结果。", {}) for _ in range(5)])

            async def offline_agent(*args, **kwargs):
                return await original(*args, **kwargs, model=model)

            monkeypatch.setattr(runtime, "run_agent", offline_agent)
            svc.agent_model_factory = lambda _: settings()
        svc.environ.update({r.bearer_env: f"fixture-{r.alias}-credential-only"
                            for r in setup.roles if r.alias != missing})
        if projection:
            # Preserve the frozen, operator-approved directory (including two distinct roles
            # for one operation). Intake still reads the contract and evaluates role readiness.
            target = built.target.model_copy(update={"business_scenario": "web_contract_v2",
                "roles": {
                role: spec.model_copy(update={"bearer_env": "AGENTTEST_TARGET_" + spec.bearer_env})
                for role, spec in built.target.roles.items()}})
            registry = product_policies(built, target.business_scenario)
            svc.intake.generic_project = lambda _: replace(built, target=target,
                registry=registry,
                registration=built.registration.model_copy(
                    update={"require_plan_review": wait_review}))
        saved = svc.intake.save(IntakeInput(name="C1.1 离线契约", document=built.document.decode(),
                                            generic=setup, confirmed=True), "operator")
        if missing:
            svc.environ[FLAG] = saved["id"]
            with pytest.raises(ValueError, match="PROJECT_INTAKE_NOT_READY"):
                await svc.submit("operator", saved["id"], task, uuid4().hex)
            assert not svc._workers
            return
        baseline_id = svc.project(saved["id"]).identity()
        if enabled:
            svc.environ[FLAG] = saved["id"]
        snapshot = svc.project(saved["id"])
        assert snapshot.identity() == baseline_id

        def focus(request):
            body = json.loads(request.content)
            public = json.loads(body["messages"][1]["content"])
            focus_requests.append((len(request.content), body, public))
            if invalid:
                return response({})
            wire = scope_reply(public, check_index(snapshot))
            if change_reply:
                change_reply(wire)
            proposals.append(deepcopy(wire))
            return response(wire)

        prepare_errors = []

        class CapturingFocus(FocusModel):
            def prepare_request(self, context):
                try:
                    return super().prepare_request(context)
                except Exception as error:
                    prepare_errors.append(error)
                    raise

        svc.focus_factory = lambda _: CapturingFocus(settings(),
            transport=httpx.MockTransport(focus), profile="generic")
        row = await svc.submit("operator", saved["id"], task, uuid4().hex)
        assert row["project_identity"] == snapshot.identity()
        context = svc.db.context(row["id"], "operator")
        expected_protocol = "compiled_release_v1" if enabled else None
        assert context.get("focus_check_protocol") == expected_protocol
        if wait_review:
            assert row["status"] == "WAITING_REVIEW"
            if enabled:
                svc.environ.pop(FLAG)
            else:
                svc.environ[FLAG] = saved["id"]
            assert svc.project(saved["id"]).identity() == baseline_id
            assert not focus_requests and not design_requests
            await svc.start(row["id"], "operator", row["plan_version"])
        worker = svc._workers.get(row["id"])
        if worker is not None:
            await asyncio.wait_for(asyncio.shield(worker), timeout=25)
        done = await finished(svc, row)
        assert svc.db.context(row["id"], "operator").get(
            "focus_check_protocol") == expected_protocol
        if prepare_errors:
            raise prepare_errors[0]
        if follow_up:
            assert done["summary"]["design_records"][0]["check_compilation"]["status"] == "APPLIED"
            focus_requests.clear()
            design_requests.clear()
            invalid = True
            child = await svc.submit("operator", saved["id"], follow_up, uuid4().hex,
                                     parent=row["id"])
            done = await finished(svc, child)
        assert not no_transport
        return done["summary"], focus_requests, proposals, design_requests, agent_requests
    finally:
        await svc.close()


@pytest.mark.asyncio
@requires_deep_agents
@pytest.mark.parametrize("variant", ["frozen", "renamed", "reverse", "add", "remove", "zero"])
async def test_task_entry_equivalence(tmp_path, observatory_project, monkeypatch, no_business,
                                      variant, record_property):
    built = transform(observatory_project, variant)
    if variant == "renamed":
        document = json.loads(built.document)
        document["paths"]["/readings/query"]["post"]["operationId"] = "readGauge"
        built = replace(built, document=json.dumps(document).encode())
    setup = setup_for(built)
    baseline, candidate = [], []
    for enabled, collection in ((False, baseline), (True, candidate)):
        # Undo only the per-run scripted Agent injection before installing the next one.
        with monkeypatch.context() as patch:
            collection.extend(await run_task(tmp_path / str(enabled), built, setup,
                enabled=enabled, projection=True, autonomy=True, no_transport=no_business,
                monkeypatch=patch))
    old, old_focus, old_wires, old_design, old_agent = baseline
    new, new_focus, new_wires, new_design, new_agent = candidate
    assert len(old_focus) == len(new_focus) == 1
    assert len(old_design) == len(new_design) == 1
    assert len(old_agent) == len(new_agent)
    assert old_focus[0][0] > new_focus[0][0]
    assert "requested_checks" in json.dumps(model_schema(old_focus[0][1]))
    assert "requested_checks" not in json.dumps(model_schema(new_focus[0][1]))
    assert "requested_checks" not in new_focus[0][1]["messages"][0]["content"]
    assert "approved_checks" in old_focus[0][2] and "approved_checks" not in new_focus[0][2]
    assert "_compiled_requested_checks" not in new_focus[0][2]
    for field in ("planning_ir", "scope_ledger", "coverage", "planning_requirements",
                  "pending_negative_values"):
        assert old.get(field) == new.get(field), field
    assert {row["policy_id"]: row for row in old["requested_checks"]} == {
        row["policy_id"]: row for row in new["requested_checks"]}
    assert old_design == new_design
    expected = set([] if variant == "zero" else HOLDOUT["metamorphic_policy_sets"].get(
        variant, HOLDOUT["cases"][0]["mapped"]))
    assert {row["policy_id"] for row in new["requested_checks"]} == expected
    assert {key for group in new["groups"] for key in group.get("policy_ids", ())} == expected
    assert new["coverage"]["requested_check_denominator"] == len(expected)
    assert not new.get("pending_negative_values") and not new["cases"]
    focus = next(row for row in new["design_records"] if row["group_id"] == "focus")
    compilation = focus["check_compilation"]
    assert compilation["protocol"] == "compiled_release_v1"
    assert compilation["status"] == "APPLIED"
    assert {mapping["policy_id"] for mapping in compilation["mappings"]} == expected
    policies = {policy.id: policy for policy in built.registry.definitions()}
    for item in compilation["mappings"]:
        approved = policies[item["policy_id"]]
        assert item["operation_id"] == approved.operation_id and item["roles"] == approved.roles
        assert item["source"]["source_ref"] == "source_3"
        assert item["source"]["clause_id"] == "request_2"
        assert item["source"]["project_identity"] == compilation["project_identity"]
    legacy_rows = [row for bundle in old_wires[0]["scope"]["operations"].values()
                   for row in bundle["requested_checks"]]
    assert len(legacy_rows) == len(expected)
    assert all("requested_checks" not in bundle
               for bundle in new_wires[0]["scope"]["operations"].values())
    record_property("focus_bytes_before_after", f"{old_focus[0][0]} / {new_focus[0][0]}")
    record_property("model_check_fields_before_after", f"{sum(map(len, legacy_rows))} / 0")
    record_property("model_check_rows_before_after", f"{len(legacy_rows)} / 0")
    record_property("scripted_calls_focus_design_agent", f"1 / 1 / {len(new_agent)}")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["current_and_future", "withdraw", "negation",
                                  "conflicting_source", "ambiguous_identity", "local", "exact",
                                  "length"])
async def test_task_entry_falls_back_before_model(tmp_path, observatory_project, no_business, case):
    task = {"local": "只检查 readPulse 的正常行为。",
            "exact": CLOSED + "limit 明确用 -2。",
            "length": CLOSED + "label 明确用长度 11。"}.get(
        case, FROZEN_VALID_CASES.get(case, {}).get("text"))
    summary, calls, _, design, _ = await run_task(tmp_path, observatory_project,
        setup_for(observatory_project), enabled=True, projection=True, task=task, invalid=True,
        no_transport=no_business)
    assert calls and not design
    assert all("focus_check_protocol" not in public for _, _, public in calls)
    assert "requested_checks" not in summary
    receipt = summary["design_records"][0]["check_compilation"]
    assert receipt["status"] == "FALLBACK"
    assert receipt["reason"] == "SCOPE_NOT_PROVEN"


@pytest.mark.asyncio
async def test_missing_identity_stops_before_task(tmp_path, observatory_project, no_business):
    await run_task(tmp_path, observatory_project, setup_for(observatory_project),
        enabled=True, projection=True, missing="curator", no_transport=no_business)


@pytest.mark.asyncio
async def test_task_entry_refuses_authority_conflict(tmp_path, observatory_project, no_business):
    denied = replace(observatory_project, target=observatory_project.target.model_copy(
        update={"allow_mutations": False}))
    summary, calls, _, design, _ = await run_task(tmp_path, denied,
        setup_for(observatory_project), enabled=True, projection=True, no_transport=no_business)
    assert not calls and not design and "requested_checks" not in summary
    focus = summary["design_records"][0]
    assert focus["diagnostic"]["code"] == "FOCUS_COMPILED_AUTHORITY_CONFLICT"
    assert focus["check_compilation"]["status"] == "REJECTED"


@pytest.mark.asyncio
@pytest.mark.parametrize(("conflict", "code"), [
    ("mapping", "FOCUS_COMPILED_CHECKS_CONFLICT"),
    ("mode", "FOCUS_COMPILED_SCOPE_CONFLICT"),
    ("normal", "FOCUS_COMPILED_SCOPE_CONFLICT"),
    ("identity", "FOCUS_COMPILED_SCOPE_CONFLICT"),
    ("source", "FOCUS_COMPILED_SCOPE_CONFLICT"),
])
async def test_task_entry_rejects_contradiction(tmp_path, observatory_project, no_business,
                                                conflict, code):
    def contradict(wire):
        bundles = wire["scope"]["operations"]
        if conflict == "mapping":
            bundles["queryReadings"]["requested_checks"] = [
                dict(check_ref="check_1", source_ref="source_3", exact_value={"value": -2})]
        elif conflict == "mode":
            wire["scope"].update(mode="all_ready", operations={})
        elif conflict == "normal":
            bundles["queryReadings"]["test_normal"] = False
        elif conflict == "identity":
            next(a for a in wire["requirement_map"]["request_1"]["actions"]
                 if a["operation_id"] == "queryReadings")["role"] = "auditor"
        else:
            bundles["queryReadings"]["existing_resource"] = True

    summary, calls, _, design, _ = await run_task(tmp_path, observatory_project,
        setup_for(observatory_project), enabled=True, projection=True, change_reply=contradict,
        no_transport=no_business)
    records = [r for r in summary["design_records"] if r["group_id"] == "focus"]
    assert not design and "requested_checks" not in summary
    assert all(public.get("focus_check_protocol") == "compiled_release_v1"
               for _, _, public in calls)
    assert records[-1]["check_compilation"]["status"] == "REJECTED"
    assert records[-1]["diagnostic"]["code"] == code
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_switch_changes_do_not_rebind_waiting_task(tmp_path, observatory_project,
                                                         no_business, enabled):
    summary, calls, _, _, _ = await run_task(tmp_path, observatory_project,
        setup_for(observatory_project), enabled=enabled, projection=True, wait_review=True,
        no_transport=no_business)
    assert len(calls) == 1 and summary["design_records"][0]["status"] == "SELECTED"
    assert ("focus_check_protocol" in calls[0][2]) is enabled
    assert ("check_compilation" in summary["design_records"][0]) is enabled


@pytest.mark.asyncio
async def test_generic_intake_without_project_projection(tmp_path, no_business):
    built, setup = project(negative=True)
    summary, calls, _, design, _ = await run_task(tmp_path, built, setup, enabled=True,
                                                no_transport=no_business)
    assert len(calls) == len(design) == 1
    assert summary["design_records"][0]["check_compilation"]["status"] == "APPLIED"
    assert len(summary["requested_checks"]) == len(check_index(built))


@pytest.mark.asyncio
async def test_followup_with_retraction_returns_to_legacy_focus(tmp_path, no_business):
    built, setup = project(negative=True)
    summary, calls, _, design, _ = await run_task(tmp_path, built, setup, enabled=True,
        follow_up="撤回上轮读操作，其他范围保持待确认。", no_transport=no_business)
    assert calls and not design and "requested_checks" not in summary
    assert all("focus_check_protocol" not in public for _, _, public in calls)
    receipt = summary["design_records"][0]["check_compilation"]
    assert receipt["status"] == "FALLBACK"
    assert receipt["reason"] == "PRIOR_SCOPE_REQUIRES_RESOLUTION"


def test_archived_legal_semantics_through_new_wire(monkeypatch, record_property):
    from agenttest.product_focus import FocusSelection, prepare_compiled_focus
    from agenttest.product_tasks import request_planning_ir

    root = Path(__file__).resolve().parents[1]
    archives = root / ".eduwork"
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from replay_focus_compiler_c1 import EXPECTATIONS, frozen_project, load

    expected = load(EXPECTATIONS)
    if not all((archives / batch / "audit.json").is_file() for batch in expected["archives"]):
        pytest.skip("Local historical evidence is not installed")
    focus = FocusModel(settings().model_copy(update={"protocol": "chatecnu"}), profile="generic")
    sizes, fields = [], []
    for name, batch in expected["archives"].items():
        assert load(archives / name / "audit.json")["qualifies"] == batch["qualifies"]
        for number, verdicts in enumerate(batch["rounds"], 1):
            if "SELECTED" not in verdicts:
                continue
            directory = archives / name / f"release-v{number}"
            phase = load(sorted(directory.glob("phase-*.json"))[-1])
            project = frozen_project(directory, phase["task_id"])
            trace = directory / "model-outputs.jsonl"
            raw = None
            for line in trace.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("model") != "focus":
                    continue
                if event.get("stage") == "decode":
                    raw = event["raw_output"]
                elif event.get("context") and event.get("output"):
                    context = event["context"]
                    original = FocusSelection.model_validate_json(focus._decode_output(
                        raw, deepcopy(context)))
                    # Evaluate paired first-call encodings. Preserve archived repair inputs
                    # on disk, but omit their legacy-only history from both measured payloads.
                    context.pop("previous_error", None)
                    context.pop("previous_order_error", None)
                    old_bytes = len(focus.prepare_request(context))
                    compiled = prepare_compiled_focus(project, context,
                        {p.id: p for p in project.registry.definitions()})
                    assert compiled["status"] == "PROPOSED"
                    wire = json.loads(raw)
                    fields.append(sum(len(row) for bundle in wire["scope"]["operations"].values()
                                      for row in bundle.pop("requested_checks")))
                    candidate_request = focus.prepare_request(context)
                    assert "requested_checks" not in json.dumps(model_schema(
                        json.loads(candidate_request)))
                    sizes.append((old_bytes, len(candidate_request)))
                    revised = FocusSelection.model_validate_json(focus._decode_output(
                        json.dumps(wire), context))
                    atoms, task = context["request_atoms"], context["user_task"]
                    assert len(revised.requested_checks) == sum(map(len, expected[
                        "requested_checks"].values()))
                    assert request_planning_ir(project, original, atoms, task, autonomous=True) == (
                        request_planning_ir(project, revised, atoms, task, autonomous=True))
    assert len(sizes) == sum("SELECTED" in verdicts for batch in expected["archives"].values()
                             for verdicts in batch["rounds"])
    assert all(candidate < baseline for baseline, candidate in sizes)
    record_property("historical_focus_bytes_by_row", str(sizes))
    record_property("historical_model_check_fields", str(fields))
