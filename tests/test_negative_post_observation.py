import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from agenttest.contracts import load_openapi
from agenttest.executor import Executor
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.negative_post_observation import observation_receipts
from agenttest.product_design import DesignCase, validate_design_case
from agenttest.product_fingerprint import scenario_fingerprint
from agenttest.product_obligations import approved_project_scope
from agenttest.product_schema import encoded_literal
from agenttest.report import ReportStore, RunReport, fingerprint
from agenttest.step_recovery import RESPONSE, StepJournal, StepScope
from agenttest.task_service import ProgressStore
from agenttest.task_store import TaskStore
from agenttest.write_effects import CREATION_UNPROVEN, NO_SIDE_EFFECT_PROVEN, for_case


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [False, True, "unknown"])
async def test_isolated_observation_counts_only_correlated_actual_requests(
        tmp_path, monkeypatch, recover):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from prepare_colleague_workspace import prepare
    from serve_colleague_targets import create_target

    receipt = await prepare(tmp_path, 18000, {}, observe_negative_posts=True)
    identifier = receipt["projects"]["parcels"]
    database = tmp_path / "workspace" / "project-intake.sqlite3"
    with sqlite3.connect(database) as connection:
        record = json.loads(connection.execute(
            "SELECT payload FROM projects WHERE id=?", (identifier,)).fetchone()[0])
    specification = record["document"]
    project = build_generic_project(identifier, specification, load_openapi(specification),
                                    GenericSetup.model_validate(record["generic"]), (), {})
    assert len(approved_project_scope(project)) == 31
    policies = {p.id: p for p in project.registry.definitions()}
    negative = next(p for p in policies.values() if p.operation_id == "createParcel"
                    and p.negative and p.negative.name == "quantity")
    body = {"name": "test", "quantity": 2, "meta": {
        "labels": ["blue"], "options": {"tracked": True}}}
    proposed = DesignCase.model_validate(dict(title="test", kind="negative", plan=dict(
        name="check", steps=[dict(id="submit", operation_id="createParcel", role=None,
        policy_id=negative.id, arguments=[
            *(dict(location="body", name=name, value=encoded_literal(value),
                   from_step=None, select=None)
              for name, value in body.items()),
            dict(location="headers", name="X-Lab-Correlation", value="a" * 32,
                 from_step=None, select=None)])])))
    events = []
    app = create_target("parcels", environ={}, observe_negative_posts=True)

    async def send(request):
        events.append(request.method)
        return await httpx.ASGITransport(app=app).handle_async_request(request)

    transport = httpx.MockTransport(send)
    store = ReportStore(tmp_path / "reports")
    namespace = ("tool", 0) if recover else None
    scenario = validate_design_case(project, proposed, store, environ={},
                                    observation_namespace=namespace)
    assert scenario_fingerprint(project, scenario) == scenario_fingerprint(
        project, validate_design_case(project, proposed, store, environ={}))
    report = RunReport(scenario_sha256="", contract_sha256="", task_mode="model_designed",
                       environment="isolated", target_origin=project.target.base_url)
    if recover:
        db = TaskStore(tmp_path / "recovery.sqlite3")
        row, _ = db.create("task", "owner", "submission", identifier, project.identity(),
                           "request", "RUNNING", {})
        authority = "deployment"
        if recover == "unknown":
            from types import SimpleNamespace

            from agenttest.product_durability import ProductDurability

            svc = SimpleNamespace(db=db, project=lambda _: project, environ={
                "AGENTTEST_HITL": "1", "AGENTTEST_DURABLE_TARGET_EPOCH": "test"},
                private_input_guard=lambda _: False)
            durable = ProductDurability(svc, row, "owner")
            authority = durable.identity
        key = StepScope(db, "task", "owner", "tool", "batch", "case", report.run_id,
                        authority, fingerprint(scenario.model_dump()))
        journal = StepJournal(key)

        class Interrupt(BaseException):
            pass

        def stop_after_negative(phase, step):
            boundary = "RESPONSE_RECEIVED" if recover == "unknown" else RESPONSE
            if phase == boundary and step.method == "POST":
                raise Interrupt()

        journal.boundary = stop_after_negative
        with pytest.raises(Interrupt):
            await Executor(project.target, project.contract, store, transport, {},
                registry=project.registry, step_journal=journal).run(scenario, report=report)
        assert events == ["GET", "POST"]
        if recover == "unknown":
            from agenttest.human_review import HumanReviews, ReviewDecision, verification_candidates
            from agenttest.step_recovery import tool_attempts
            from agenttest.task_service import TaskService

            durable.publish("TOOL", summary={}, checkpoint_id="test",
                state=dict(tool_key="tool", usage=dict(requests=0, usage_known=True),
                    design=dict(group_request_limit=3,
                                recovery_probes=verification_candidates(project, [scenario]))))
            db.transition("task", "owner", "RUNNING", "INTERRUPTED")
            durable.unknown_reviews()
            reviews = HumanReviews(db)
            review, = reviews.list("task", "owner")
            assert review["status"] == "REVIEW_REQUIRED"
            assert review["allowed_decisions"] == ["archive", "request_verification"]
            with pytest.raises(ValueError, match="REVIEW_DECISION_NOT_ALLOWED"):
                reviews.decide("task", "owner", review, ReviewDecision(
                    decision="approve", version=1))
            requested, changed = reviews.decide("task", "owner", review, ReviewDecision(
                decision="request_verification", version=1))
            assert changed and requested["status"] == "VERIFICATION_REQUESTED"
            attempts = tool_attempts(db, "task", "tool")
            verification = durable.verification_plan(db.durable_state("task", "owner"),
                                                     attempts, attempts[-1])
            service = SimpleNamespace(db=db, reviews=reviews, project=lambda _: project,
                                      _serial=asyncio.Semaphore(1),
                                      reports=store, environ={})
            monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **_: transport)
            await TaskService.verify_write(service, durable, review, verification)
            assert events == ["GET", "POST", "GET"]
            observed, = reviews.list("task", "owner")
            assert observed["status"] == "REVIEW_REQUIRED"
            assert observed["allowed_decisions"] == ["archive"]
            assert observed["verification"]["requests"] == 1
            assert observed["verification"]["observations"][0]["http_status"] == [200]
            assert tool_attempts(db, "task", "tool")[-1]["phase"] == "DISPATCHED"
            assert not db.get("task", "owner")["summary"].get("complete")
            return
        restored = DesignCase.model_validate_json(proposed.model_dump_json())
        rebuilt = validate_design_case(project, restored, store, environ={},
                                       observation_namespace=namespace)
        assert rebuilt == scenario
        independent = validate_design_case(project, restored, store, environ={},
                                           observation_namespace=("tool", 1))
        assert independent.steps[0].query != rebuilt.steps[0].query
        journal = StepJournal(key)
        resumed = ProgressStore(store.root, lambda *_: None, step_journal=journal)
        report = RunReport(scenario_sha256="", contract_sha256="", task_mode="model_designed",
            environment="isolated", target_origin=project.target.base_url,
            run_id=key.run_id, revision=store.read(key.run_id).revision)
        report = await Executor(project.target, project.contract, resumed, transport, {},
            registry=project.registry, step_journal=journal).run(rebuilt, report=report)
    else:
        report = await Executor(project.target, project.contract, store, transport, {},
            registry=project.registry).run(scenario, report=report)
    case = dict(evidence=[dict(step_id=result.step_id, operation_id=result.operation_id,
        method=result.method, policy_id=planned.policy_id, role=planned.role,
        outcome=result.outcome.value, executed=bool(result.attempts),
        attempt=result.attempts[-1].model_dump(), request=result.request_evidence,
        checks=[check.model_dump() for check in result.checks])
        for result, planned in zip(report.steps, scenario.steps, strict=True)])
    assert events == ["GET", "POST", "GET"]
    assert [step.attempts[-1].status_code for step in report.steps] == [200, 422, 200]
    assert report.policy_authorized and report.outcome.value == "PASS"
    case["negative_post_observations"] = observation_receipts(project, scenario, case)
    assert for_case(case, policies)[0]["status"] == NO_SIDE_EFFECT_PROVEN

    count = next(check for check in case["evidence"][-1]["checks"]
                 if check["selector"] == "$.count")
    count["passed"] = False
    case["negative_post_observations"] = observation_receipts(project, scenario, case)
    assert for_case(case, policies)[0]["status"] == CREATION_UNPROVEN
    count["passed"] = True

    case["evidence"][-1]["request"]["values"]["query"]["key"] = "other"
    case["negative_post_observations"] = observation_receipts(project, scenario, case)
    assert for_case(case, policies)[0]["status"] == CREATION_UNPROVEN

    create = next(p for p in policies.values()
                  if p.operation_id == "createParcel" and p.negative is None)
    change = next(p for p in policies.values() if p.operation_id == "changeParcel"
                  and p.negative and p.negative.name == "name")
    seed = proposed.plan.steps[0].model_dump()
    seed.update(id="owned", policy_id=create.id)
    prepared = DesignCase.model_validate(dict(title="bound negative", kind="negative",
        plan=dict(name="bound", steps=[seed, dict(id="edit", operation_id="changeParcel",
            role=None, policy_id=change.id, arguments=[
                dict(location="path_parameters", name="parcelId", value=None,
                     from_step="owned", select="$.id"),
                dict(location="body", name="name", value="test", from_step=None, select=None)
            ])])))
    closed = validate_design_case(project, prepared, store, environ={}, verify_batch_cleanup=True)
    assert [step.operation_id for step in closed.steps] == [
        "createParcel", "changeParcel", "deleteParcel", "readParcel"]
    assert all(step.path_parameters["parcelId"].from_step == "owned"
               for step in closed.steps[1:])
    lifetime = await Executor(project.target, project.contract, store, transport, {},
        registry=project.registry).run(closed)
    assert [step.attempts[-1].status_code for step in lifetime.steps] == [201, 422, 204, 404]
    assert lifetime.policy_authorized and lifetime.outcome.value == "PASS"
    assert not app.state.records

    from dataclasses import replace

    from agenttest.policies import NeedsReview, PolicyRegistry

    deletion = policies[closed.steps[-2].policy_id]
    ambiguous = replace(project, registry=PolicyRegistry(
        [*policies.values(), deletion.model_copy(update={"id": "another_delete_rule"})]))
    with pytest.raises(NeedsReview, match="DESIGN_SELECTED_CLEANUP_MISSING"):
        validate_design_case(ambiguous, prepared, store, environ={}, verify_batch_cleanup=True)
