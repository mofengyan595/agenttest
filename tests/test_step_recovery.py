"""Recovery of trusted HTTP receipts across executor and process boundaries."""

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from test_product_agent import scripted, tool_turn
from test_product_design import settings
from test_product_supervisor import lifecycle_project
from test_resource_lifecycle import executor, plan
from test_task_completion import project_with_absence

from agenttest.dsl import Scenario
from agenttest.report import AttemptEvidence, Outcome, RunReport, StepResult, fingerprint
from agenttest.step_recovery import StepJournal, StepRecoveryBlocked, StepScope, tool_attempts
from agenttest.task_service import ProgressStore, TaskService
from agenttest.task_store import TERMINAL, TaskStore


@pytest.mark.asyncio
async def test_report_without_trusted_step_ledger_cannot_resume(tmp_path):
    runner = executor(tmp_path, lambda request: pytest.fail("Unexpected HTTP"))
    report = RunReport(scenario_sha256="", contract_sha256="", environment="isolated",
                       target_origin=runner.config.base_url)
    report.steps.append(StepResult(step_id="create", operation_id="insertJob",
        method="POST", path_template="/jobs", outcome=Outcome.PASS,
        reason="POLICY_EVALUATED", policy_sha256="0" * 64,
        attempts=[AttemptEvidence(attempt=1, elapsed_ms=0, status_code=201)]))
    runner.store.save(report)
    with pytest.raises(ValueError, match="RUN_NOT_RESUMABLE"):
        await runner.run(plan(), report=report)


def child(root, port, end, method, mode="executor"):
    from agenttest.workflow import FakeLLM, Workflow, WorkflowBudget

    project = project_with_absence()
    target = project.target.model_copy(update=dict(base_url=f"http://127.0.0.1:{port}"))
    policies = {policy.operation_id: policy.id for policy in project.registry.definitions()
                if policy.id != "web_rule_7"}
    scenario = Scenario.model_validate(dict(name="owned lifecycle", steps=[
        dict(id="create", operation_id="createParcel", policy_id=policies["createParcel"],
             body=dict(name=dict(value="test"), quantity=dict(value=1))),
        dict(id="delete", operation_id="deleteParcel", policy_id=policies["deleteParcel"],
             path_parameters=dict(parcelId=dict(from_step="create", select="$.id"))),
        dict(id="absent", operation_id="readParcel", policy_id="web_rule_7",
             path_parameters=dict(parcelId=dict(from_step="create", select="$.id")))]))
    def interrupt(_journal, phase, result):
        if end == "SERVER_EFFECT" and phase == "DISPATCHED" and result.method == method:
            def wait_for_server_commit():
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    if (root / "server-committed").is_file():
                        os._exit(89)
                    time.sleep(0.01)

            Thread(target=wait_for_server_commit, daemon=True).start()
        if phase == end and result.method == method:
            os._exit(89)

    StepJournal.boundary = interrupt
    if end == "BEFORE_TOOL_DONE":
        from agenttest.product_durability import ProductDurability

        publish = ProductDurability.publish

        def interrupt_publish(durable, phase, **kwargs):
            if phase == "TOOL_DONE":
                os._exit(89)
            return publish(durable, phase, **kwargs)

        ProductDurability.publish = interrupt_publish
    if mode == "product":
        asyncio.run(run_product(root, target, scenario, project, end != "no-fault"))
        return
    db = TaskStore(root / "tasks.sqlite3")
    db.create("task", "operator", "submission", "parcels", "identity",
              "request", "RUNNING", {})
    scope = StepScope(db, "task", "operator", "tool", "group", "case", "f" * 32, "deployment",
                      fingerprint(scenario.model_dump()))
    journal = StepJournal(scope)
    store = ProgressStore(root / "reports", lambda *_: None, step_journal=journal)
    try:
        result = asyncio.run(Workflow(project.document, target, project.registry, FakeLLM(""),
            store, task_mode="model_designed", prepared_plan=scenario,
            step_journal=journal, budget=WorkflowBudget(max_model_calls=0)).run())
    except StepRecoveryBlocked as exc:
        print(str(exc))
        return
    print(result.model_dump_json())


async def run_product(root, target, scenario, project, first):
    import agenttest.product_agent as agent
    import agenttest.product_tasks as tasks
    from agenttest.product_design import TestDesign
    from agenttest.product_schema import encoded_literal
    from agenttest.project_intake import IntakeInput
    from agenttest.report import ReportStore

    run_agent = agent.run_agent
    messages = [tool_turn("run_test_group", "call_one", group_id="group_1")] if first else []
    model, _ = scripted(*messages, ("已核对报告。", {}))

    async def offline_agent(*args, **kwargs):
        return await run_agent(*args, model=model, **kwargs)

    async def design(*_args, **_kwargs):
        if not first:
            pytest.fail("Recovery requested another Design model call")
        steps = [dict(id=step.id, operation_id=step.operation_id, role=None,
            policy_id=step.policy_id, arguments=[
                dict(location=location, name=name,
                     value=encoded_literal(binding.value) if binding.from_step is None else None,
                     from_step=binding.from_step,
                     select=binding.select if binding.from_step else None)
                for location in ("body", "path_parameters")
                for name, binding in getattr(step, location).items()])
            for step in scenario.steps]
        return TestDesign.model_validate(dict(interpretation="执行批准的同资源生命周期",
            cases=[dict(title="删除后回读", kind="flow", plan=dict(name="owned", steps=steps))],
            questions=[], not_covered=[]))

    agent.run_agent = offline_agent
    tasks.propose_design = design
    db = TaskStore(root / "tasks.sqlite3")
    svc = TaskService({}, db, ReportStore(root / "reports"), lambda _: None,
        generic_intake=True, designer_factory=lambda _: object(),
        agent_model_factory=lambda _: settings(), model_status=lambda: "CONFIGURED_MODEL",
        environ=dict(AGENTTEST_DURABLE_EXECUTION="1", AGENTTEST_DURABLE_TARGET_EPOCH="test"))
    lookup = svc.project
    svc.project = lambda key: replace(lookup(key), registry=project.registry)
    path = root / "task-id"
    try:
        if first:
            _, setup = lifecycle_project()
            setup.base_url = target.base_url
            record = svc.intake.save(IntakeInput(name="Parcel lab",
                document=project.document.decode(), generic=setup, confirmed=True), "operator")
            row = await svc.submit("operator", record["id"], tasks.DEFAULT_ROUND, "request")
            path.write_text(row["id"], encoding="ascii")
        else:
            row = await svc.resume(path.read_text(encoding="ascii"), "operator")
        await svc._workers[row["id"]]
        row = db.get(row["id"], "operator")
        assert row["status"] in TERMINAL
        print(json.dumps(row, ensure_ascii=False))
    finally:
        await svc.close()


@pytest.mark.parametrize(("phase", "method", "fake_delete", "can_resume", "before_count"), [
    ("BEFORE_PREPARED", "POST", False, True, 0),
    ("PREPARED", "POST", False, True, 0),
    ("DISPATCHED", "POST", False, False, 0),
    ("SERVER_EFFECT", "POST", False, False, 1),
    ("RESPONSE_RECEIVED", "POST", False, False, 1),
    ("RESPONSE_RECORDED", "POST", False, True, 1),
    ("EVIDENCE_COMMITTED", "POST", False, True, 1),
    ("RESPONSE_RECORDED", "DELETE", False, True, 2),
    ("RESPONSE_RECORDED", "DELETE", True, True, 2),
    ("EVIDENCE_COMMITTED", "GET", False, True, 3),
    ("REPEATED_CRASH", "POST", False, True, 1),
    ("PRODUCT_RECOVERY", "POST", False, True, 1),
    ("BEFORE_TOOL_DONE", "GET", False, True, 3),
])
def test_process_exit_at_execution_boundary(
        tmp_path, phase, method, fake_delete, can_resume, before_count):
    pytest.importorskip("langgraph")
    product_mode = phase in {"PRODUCT_RECOVERY", "BEFORE_TOOL_DONE"}
    if product_mode:
        pytest.importorskip("deepagents")
        pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    events = []
    inventory = set()
    release_response = Event()

    class Target(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            inventory.add("new_resource")
            if phase == "SERVER_EFFECT":
                events.append((self.command, self.path, None))
                (tmp_path / "server-committed").touch()
                release_response.wait(20)
                return
            self.reply(201, {"id": "new_resource"})

        def do_DELETE(self):
            if not fake_delete:
                inventory.remove(self.path.rsplit("/", 1)[-1])
            self.reply(204, None)

        def do_GET(self):
            existing = self.path.rsplit("/", 1)[-1] in inventory
            self.reply(200 if existing else 404, {"id": "new_resource"} if existing else {})

        def reply(self, status, value):
            events.append((self.command, self.path, status))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if value is not None:
                self.wfile.write(json.dumps(value).encode())

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Target) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            args = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), str(tmp_path),
                    str(server.server_address[1])]
            mode = "product" if product_mode else "executor"
            first_phase = ("RESPONSE_RECORDED" if phase in {"REPEATED_CRASH", "PRODUCT_RECOVERY"}
                           else phase)
            interrupted = subprocess.run([*args, first_phase, method, mode], capture_output=True,
                                         timeout=60, encoding="utf-8", check=False)
            assert interrupted.returncode == 89, interrupted.stderr or interrupted.stdout
            assert len(events) == before_count
            release_response.set()
            if phase == "BEFORE_TOOL_DONE":
                task = (tmp_path / "task-id").read_text(encoding="ascii")
                db = TaskStore(tmp_path / "tasks.sqlite3")
                row = db.get(task, "operator")
                run_id = row["summary"]["cases"][0]["run_id"]
                assert db.product_asset(task, run_id)[1]["snapshot"]["source_run_id"] == run_id
            if phase == "REPEATED_CRASH":
                again = subprocess.run([*args, "RESPONSE_RECORDED", "DELETE"],
                    capture_output=True, timeout=60, encoding="utf-8", check=False)
                assert again.returncode == 89, again.stderr or again.stdout
                assert len(events) == 2
            resumed = subprocess.run([*args, "no-fault", method, mode], capture_output=True,
                                     timeout=60, encoding="utf-8", check=True)
        finally:
            release_response.set()
            server.shutdown()
            worker.join()
    db = TaskStore(tmp_path / "tasks.sqlite3")
    if product_mode:
        product = json.loads(resumed.stdout)
        assert product["status"] == "FINISHED", resumed.stderr or product["summary"]
        assert product["summary"]["context_usage"]["requests"] == 3, product["summary"]
        assert product["summary"]["cases"][0]["outcome"] == "PASS", product["summary"]
        assert [event[0] for event in events] == ["POST", "DELETE", "GET"]
        assert not inventory
        return
    rows = tool_attempts(db, "task", "tool")
    if not can_resume:
        assert resumed.stdout.strip() == "DURABLE_WRITE_OUTCOME_REQUIRES_REVIEW"
        assert len(events) == before_count and rows[0]["phase"] == "DISPATCHED"
        assert bool(inventory) == bool(before_count)
        return
    result = RunReport.model_validate_json(resumed.stdout)
    assert result.run_id == "f" * 32
    assert [row["phase"] for row in rows] == ["EVIDENCE_COMMITTED"] * 3
    assert events == [
        ("POST", "/parcels", 201), ("DELETE", "/parcels/new_resource", 204),
        ("GET", "/parcels/new_resource", 200 if fake_delete else 404)]
    assert result.outcome == (Outcome.ASSERTION_FAILED if fake_delete else Outcome.PASS)
    assert bool(inventory) == fake_delete


if __name__ == "__main__":
    child(Path(sys.argv[1]), int(sys.argv[2]), *sys.argv[3:])
