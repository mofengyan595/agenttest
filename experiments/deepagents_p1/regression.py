"""The delete-404 precondition regression, run against the real isolated lab.

P0's confirmed defect was a 404 accusation that the plan itself had created. The P1 brief
requires three situations to stay distinct, and each one needs *real* evidence rather than a
unit test:

* **S1 preflight** -- the plan asserts the parcel is gone but never deletes it: refused before
  any HTTP request is sent;
* **S2 runtime** -- the plan deletes the parcel and the deletion fails, so the "gone" state is
  never established: the readback mismatch is ``NEEDS_REVIEW``, never an independent business
  defect;
* **S3 established** -- the deletion succeeds and the readback still violates the approved
  expectation: only then is ``ASSERTION_FAILED`` valid evidence. The mirror case (delete, then
  expect the parcel to still be readable) is refused at preflight for the same reason as S1;
* **S4 lying deletion** -- the lab answers ``204`` and keeps the record, so the readback finds a
  parcel that should be gone. This must stay ``ASSERTION_FAILED``: the fix removes plan-induced
  accusations, never real ones;
* **S5 literal-id probe** -- an absence expectation on an id no step produced. The approved
  policy requires the binding, so the product refuses it earlier
  (``POLICY_DEPENDENCY_MISMATCH``); the ``UNPROVEN`` branch is therefore defensive.

The lab approves a ``404`` readback policy so an absence expectation is an *operator-approved*
status, not something this script invents. ``--delete-fault`` starts a second lab whose DELETE
fails with 503, and ``--silent-port`` a third whose DELETE answers 204 without deleting; those
are the only ways to measure S2 and S4 end to end.

Run from the repository root:

    .\\.venv\\Scripts\\python.exe experiments/deepagents_p1/regression.py ^
        --out .eduwork/runs/p1/precondition-regression.json
"""

import argparse
import asyncio
import importlib.util
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TARGET_SCRIPTS = ROOT / "scripts" / "serve_colleague_targets.py"


def load_targets():
    spec = importlib.util.spec_from_file_location("colleague_targets", TARGET_SCRIPTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def absence_project(base_url: str, *, established_by: str = "web_rule_3"):
    """The parcels project plus one operator-approved absence expectation.

    ``web_rule_7`` is an ordinary approved policy in every respect -- reviewed, sourced,
    same operation, same bindings -- whose approved status is ``404``. Nothing in the product
    is special-cased for it: it is the operator saying "after this parcel is deleted, reading
    it must answer 404".
    """
    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import GenericSetup, analyze_contract, build_generic_project
    from agenttest.policies import PolicyRegistry

    targets = load_targets()
    document = json.dumps(targets.document("parcels"), ensure_ascii=False)
    contract = load_openapi(document)
    suggestions = analyze_contract(contract)
    operations = [dict(operation_id=item["operation_id"], alias=item["alias"],
                       statuses=item["statuses"],
                       arguments=[{key: value for key, value in argument.items()
                                   if key in {"location", "name", "value", "source_operation",
                                              "selector", "generated", "needs_input"}}
                                  for argument in item["arguments"]])
                  for item in suggestions]
    setup = GenericSetup.model_validate(dict(
        base_url=base_url, isolated_confirmed=True, allow_mutations=True, assisted=True,
        approve_negative_tests=False, operations=operations))
    project = build_generic_project("parcels", document, contract, setup)
    definitions = list(project.registry.definitions())
    base = next(item for item in definitions if item.operation_id == "readParcel")
    status = base.assertions.status.model_copy(update=dict(codes=[404]))
    absence = base.model_copy(update=dict(
        id="web_rule_7",
        assertions=base.assertions.model_copy(update=dict(
            status=status,
            source=(f"operator: after {established_by} deletes the parcel, reading the same "
                    "parcel must answer 404")))))
    registry = PolicyRegistry(definitions + [absence])
    return replace(project, registry=registry), absence.id


def scenarios(project, absence_policy_id: str):
    """The four plans, written with the DSL the executor already accepts."""
    from agenttest.dsl import ArgumentBinding, Scenario, Step

    policies = {policy.operation_id + "/" + ("absent" if policy.id == absence_policy_id
                                             else "normal"): policy.id
                for policy in project.registry.definitions() if policy.id != absence_policy_id}
    create = policies["createParcel/normal"]
    delete = policies["deleteParcel/normal"]
    read = policies["readParcel/normal"]
    read_absent = absence_policy_id

    def create_step(identifier="create"):
        # The DSL body is a flat field map; a nested schema object is one binding whose value
        # is the whole object, validated against the contract like any other literal.
        return Step(id=identifier, operation_id="createParcel", role=None, policy_id=create,
                    body={"name": ArgumentBinding(value="parcel-" + identifier),
                          "quantity": ArgumentBinding(value=1),
                          "meta": ArgumentBinding(value={"labels": ["blue"],
                                                         "options": {"tracked": True}})})

    def delete_step(identifier="delete", from_step="create"):
        return Step(id=identifier, operation_id="deleteParcel", role=None, policy_id=delete,
                    path_parameters={"parcelId": ArgumentBinding(
                        from_step=from_step, select="$.id")})

    def read_step(identifier="read", policy_id=read, from_step="create"):
        return Step(id=identifier, operation_id="readParcel", role=None, policy_id=policy_id,
                    path_parameters={"parcelId": ArgumentBinding(
                        from_step=from_step, select="$.id")})

    return dict(
        s1_missing_precondition=Scenario(name="S1 断言不存在但没有删除", steps=[
            create_step(), read_step(policy_id=read_absent)]),
        s1_mirror_expects_present=Scenario(name="S1 删除后仍期望可读", steps=[
            create_step(), delete_step(), read_step(policy_id=read)]),
        s2_delete_failed=Scenario(name="S2 删除失败后的回读", steps=[
            create_step(), delete_step(), read_step(policy_id=read_absent)]),
        s3_established=Scenario(name="S3 删除成功后的回读", steps=[
            create_step(), delete_step(), read_step(policy_id=read_absent)]),
        s5_literal_probe=Scenario(name="S5 字面 id 的不存在探针", steps=[
            Step(id="read", operation_id="readParcel", role=None, policy_id=read_absent,
                 path_parameters={"parcelId": ArgumentBinding(value="never-existed-parcel")})]),
    )


def serve(port: int, *, delete_fault_status=None, delete_noop=False):
    import uvicorn

    targets = load_targets()
    app = targets.create_target("parcels", environ={},
                               delete_fault_status=delete_fault_status,
                               delete_noop=delete_noop)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           proxy_headers=False, access_log=False))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def wait_for(url: str, seconds: float = 15.0) -> None:
    import time
    import urllib.error
    import urllib.request

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/openapi.json", timeout=1)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError("lab target did not start")


async def attempt(project, scenario, root: Path, label: str) -> dict:
    """Preflight, then -- only if it passes -- execute the plan through the real executor."""
    from agenttest.product_preconditions import (
        precondition_report,
        reclassify_unestablished,
        validate_expectation_preconditions,
    )
    from agenttest.report import Outcome, ReportStore, aggregate
    from agenttest.workflow import Workflow, WorkflowBudget

    record = dict(label=label, scenario=scenario.name,
                  steps=[step.operation_id for step in scenario.steps])
    try:
        audit = validate_expectation_preconditions(project, scenario)
    except Exception as exc:
        record.update(preflight="REFUSED", refusal=getattr(
            getattr(exc, "diagnostic", None), "code", None) or type(exc).__name__,
            audit=precondition_report(project, scenario))
        return record
    record.update(preflight="ACCEPTED", audit=audit)
    store = ReportStore(root / "reports")
    from agenttest.workflow import FakeLLM

    workflow = Workflow(project.document, project.target, project.registry, FakeLLM(""), store,
                        environ={}, task_mode="model_designed",
                        user_task="S1-S3 前置条件回归", prepared_plan=scenario,
                        plan_derivation=dict(source="PRECONDITION_REGRESSION"),
                        budget=WorkflowBudget(max_model_calls=0))
    report = await workflow.run()
    changes = reclassify_unestablished(report, scenario, project)
    if changes:
        report.outcome = aggregate(report.steps)
        report.reason = changes[0]["reason"]
        store.save(report)
    record.update(run_id=report.run_id, outcome=report.outcome.value,
                  reason=report.reason,
                  http_requests=sum(len(step.attempts) for step in report.steps),
                  steps_outcome=[dict(step=step.step_id, operation=step.operation_id,
                                      outcome=step.outcome.value,
                                      status=[attempt.status_code
                                              for attempt in step.attempts])
                                 for step in report.steps],
                  reclassified=changes,
                  audit_after=precondition_report(project, scenario),
                  failed_checks=[dict(step=step.step_id, kind=getattr(check.kind, "value",
                                                                     check.kind),
                                      expected=check.expected, actual=check.actual)
                                 for step in report.steps for check in step.checks
                                 if not check.passed],
                  outcome_is_assertion_failed=report.outcome == Outcome.ASSERTION_FAILED)
    return record


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--port", type=int, default=18993)
    parser.add_argument("--fault-port", type=int, default=18994)
    parser.add_argument("--silent-port", type=int, default=18995)
    args = parser.parse_args()
    root = Path(args.out).parent
    root.mkdir(parents=True, exist_ok=True)

    healthy = serve(args.port)
    faulted = serve(args.fault_port, delete_fault_status=503)
    silent = serve(args.silent_port, delete_noop=True)
    wait_for(f"http://127.0.0.1:{args.port}")
    wait_for(f"http://127.0.0.1:{args.fault_port}")
    wait_for(f"http://127.0.0.1:{args.silent_port}")
    healthy_project, absence_id = absence_project(f"http://127.0.0.1:{args.port}")
    faulted_project, faulted_absence = absence_project(f"http://127.0.0.1:{args.fault_port}")
    silent_project, silent_absence = absence_project(f"http://127.0.0.1:{args.silent_port}")
    plans = scenarios(healthy_project, absence_id)
    faulted_plans = scenarios(faulted_project, faulted_absence)
    silent_plans = scenarios(silent_project, silent_absence)

    records = [await attempt(healthy_project, plans["s1_missing_precondition"], root,
                             "S1 absence expectation without a deletion"),
               await attempt(healthy_project, plans["s1_mirror_expects_present"], root,
                             "S1 delete then expect readable"),
               await attempt(faulted_project, faulted_plans["s2_delete_failed"], root,
                             "S2 deletion failed, absence never established"),
               await attempt(healthy_project, plans["s3_established"], root,
                             "S3 deletion passed, absence established"),
               await attempt(silent_project, silent_plans["s3_established"], root,
                             "S4 deletion claims success but keeps the record"),
               await attempt(healthy_project, plans["s5_literal_probe"], root,
                             "S5 literal-id absence probe")]
    for server in (healthy, faulted, silent):
        server.should_exit = True

    payload = dict(absence_policy=absence_id, records=records,
                   ports=dict(healthy=args.port, faulted=args.fault_port,
                              silent=args.silent_port))
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    for record in records:
        print(json.dumps(dict(label=record["label"], preflight=record["preflight"],
                              refusal=record.get("refusal"), outcome=record.get("outcome"),
                              reason=record.get("reason"),
                              http=record.get("http_requests"),
                              reclassified=[item["step_id"]
                                            for item in record.get("reclassified") or []]),
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
