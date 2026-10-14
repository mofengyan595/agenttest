"""Offline cross-project mechanism checks; fixed replies never measure model quality."""

import hashlib
import json
import re
import secrets
from pathlib import Path

import httpx

from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.models import PlanEvidence
from agenttest.eval_v2.scoring import capture_plan, score_intent
from agenttest.evaluation import save_evaluation
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner, PlannerDraft
from agenttest.mini_target import create_target
from agenttest.policies import PolicyRegistry
from agenttest.report import Outcome, ReportStore, fingerprint
from agenttest.safety import TargetConfig
from agenttest.task_contract import TaskContract, task_validator
from agenttest.ticket_target import create_ticket_target

EXAMPLES = PROJECT_ROOT / "examples/portability"


def project_inputs(project: str, fault: str = "none"):
    """Only this demo factory knows the two disposable targets; the agent does not."""
    if project == "courses":
        environ = {"DEMO_ALICE_USERNAME": "alice", "DEMO_BOB_USERNAME": "bob",
                   "DEMO_ALICE_PASSWORD": secrets.token_urlsafe(24),
                   "DEMO_BOB_PASSWORD": secrets.token_urlsafe(24)}
        app = create_target({"alice": environ["DEMO_ALICE_PASSWORD"],
                             "bob": environ["DEMO_BOB_PASSWORD"]}, fault)
    elif project == "tickets":
        environ = {"TICKET_OWNER_BEARER": secrets.token_urlsafe(24),
                   "TICKET_VISITOR_BEARER": secrets.token_urlsafe(24)}
        app = create_ticket_target({"owner": environ["TICKET_OWNER_BEARER"],
                                    "visitor": environ["TICKET_VISITOR_BEARER"]}, fault)
    else:
        raise ValueError("Unknown disposable project")
    directory = EXAMPLES / project
    document = (directory / "openapi.json").read_bytes()
    contract = load_openapi(document)
    if contract.model_dump() != load_openapi(json.dumps(app.openapi())).model_dump():
        raise ValueError("Disposable target differs from its versioned contract")
    config = TargetConfig.model_validate_json((directory / "target.json").read_bytes())
    registry = PolicyRegistry.from_json((directory / "policies.json").read_bytes())
    return app, document, config, registry, environ


def task_inputs(project: str, name: str):
    if (project, name) not in {("courses", "read"), ("courses", "ownership"),
                              ("tickets", "read"), ("tickets", "ownership"),
                              ("tickets", "close")}:
        raise ValueError("Unknown development fixture")
    task = TaskContract.model_validate_json(
        (EXAMPLES / project / (name + ".task.json")).read_bytes())
    draft = PlannerDraft.model_validate_json(
        (EXAMPLES / project / (name + ".draft.json")).read_bytes())
    return task, draft


def cases() -> list[dict]:
    rows = []
    for project, name in (("courses", "read"), ("courses", "ownership"),
                          ("tickets", "read"), ("tickets", "ownership"), ("tickets", "close")):
        rows.append(dict(project=project, task=name, fault="none", mutation=None,
                         expected="PASS", diagnostic=None, failed_check=None))
    rows.append(dict(project="courses", task="ownership", fault="none", mutation="interleave",
                     expected="PASS", diagnostic=None, failed_check=None))
    for project, name, fault, operation, kind, selector in (
        ("courses", "read", "response_name", "getCourse", "equals", "$.name"),
        ("courses", "ownership", "ownership", "getCourse", "http_status", None),
        ("tickets", "read", "wrong_subject", "readTicket", "equals", "$.payload.subject"),
        ("tickets", "ownership", "owner_leak", "readTicket", "http_status", None),
        ("tickets", "close", "close_ignored", "closeTicket", "equals", "$.payload.state"),
    ):
        rows.append(dict(project=project, task=name, fault=fault, mutation=None,
                         expected="ASSERTION_FAILED", diagnostic=None,
                         failed_check=[operation, kind, selector]))
    for name, mutation, code in (
        ("read", "extra_list", "INTENT_TASK_CONTRACT_PLAN_MISMATCH"),
        ("close", "missing_close", "INTENT_TASK_CONTRACT_PLAN_MISMATCH"),
        ("read", "wrong_selector", "POLICY_DEPENDENCY_MISMATCH"),
        ("read", "wrong_literal", "INTENT_TASK_CONTRACT_PLAN_MISMATCH"),
    ):
        rows.append(dict(project="tickets", task=name, fault="none", mutation=mutation,
                         expected="NEEDS_REVIEW", diagnostic=code, failed_check=None))
    return [{"case_id": f"G{i:03}", **row} for i, row in enumerate(rows, 1)]


def fixture_draft(case: dict) -> PlannerDraft:
    """Read explicit, pre-authored replies; mutations exercise gates, not model reasoning."""
    _, draft = task_inputs(case["project"], case["task"])
    data = draft.model_dump()
    mutation = case["mutation"]
    if mutation == "interleave":
        data["steps"].insert(1, data["steps"].pop(3))
        for step in data["steps"]:
            step["id"] = "renamed_" + step["id"]
            for argument in step["arguments"]:
                if argument["from_step"]:
                    argument["from_step"] = "renamed_" + argument["from_step"]
    elif mutation == "extra_list":
        data["steps"].append(dict(id="extra", operation_id="listTickets", role="owner",
                                  policy_id="ticket_list_v1", arguments=[]))
    elif mutation == "missing_close":
        data = task_inputs("tickets", "read")[1].model_dump()
    elif mutation == "wrong_selector":
        data["steps"][1]["arguments"][0]["select"] = "$.courseId"
    elif mutation == "wrong_literal":
        data["steps"][0]["arguments"][0]["value"] = "未经请求的主题"
    return PlannerDraft.model_validate(data)


def grade(case, task, report, plan):
    """Verify the intended failure mechanism; an arbitrary failure is not a detected fault."""
    identity = (report.user_task_sha256 == fingerprint(task.user_task)
                and report.workflow_completed is True and report.task_mode == "natural_language")
    requests = sum(len(s.attempts) for s in report.steps)
    if case["expected"] == "NEEDS_REVIEW":
        verified = (identity and report.outcome == Outcome.NEEDS_REVIEW and not requests
                    and report.tool_calls == 0 and not report.intent_verified
                    and report.task_satisfied is False and report.diagnostic is not None
                    and report.diagnostic.code == case["diagnostic"])
    else:
        matched = (report.scenario_sha256 == plan.scenario_sha256
                   and [s.model_dump() for s in report.plan_structure] == [
                       {key: getattr(s, key) for key in (
                           "step_id", "operation_id", "role", "policy_id", "argument_sha256",
                           "dependencies")} for s in plan.steps])
        verified = (identity and matched and report.dsl_valid and report.policy_authorized
                    and report.intent_verified and score_intent(task.requirements, plan).passed
                    and report.outcome.value == case["expected"] and report.tool_calls == 1)
        if case["expected"] == "PASS":
            verified = (verified and report.task_satisfied is True
                        and len(report.steps) == len(plan.steps)
                        and all(s.attempts and s.checks and all(c.passed for c in s.checks)
                                for s in report.steps))
        else:
            verified = (verified and report.task_satisfied is False and any(
                [s.operation_id, c.kind, c.selector] == case["failed_check"] and not c.passed
                for s in report.steps for c in s.checks))
    return {"verified": bool(verified), "outcome": report.outcome.value,
            "requests": requests, "task_satisfied": report.task_satisfied}


def summarize(rows):
    return {"schema_version": "agenttest.portability.development.v1",
            "classification": "FIXED_REPLY_MECHANISM_CHECK_ONLY", "human_reviewed": 0,
            "formal_eval_v2_score": None, "real_model_calls": 0, "real_java_requests": 0,
            "real_database_writes": 0, "projects": sorted({r["project"] for r in rows}),
            "cases": len(rows), "verified": all(r["grade"]["verified"] for r in rows),
            "normal_cases": sum(r["expected"] == "PASS" for r in rows),
            "fault_cases": sum(r["expected"] == "ASSERTION_FAILED" for r in rows),
            "preflight_rejections": sum(r["expected"] == "NEEDS_REVIEW" for r in rows),
            "results": rows}


async def run_portability(output: Path, sources=("fake", "mock_transport")):
    from agenttest.development_ab import private_output
    from agenttest.workflow import FakeLLM, Workflow

    if not sources or len(set(sources)) != len(sources) or not set(sources) <= {
        "fake", "mock_transport"
    }:
        raise ValueError("This acceptance runner is offline only")
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    specifications = cases()
    tasks = {f"{c['project']}/{c['task']}": task_inputs(c["project"], c["task"])[0].model_dump(
        mode="json") for c in specifications}
    frozen = {"schema_version": "agenttest.portability.freeze.v1", "sources": list(sources),
              "cases": specifications, "tasks": tasks, "git": checkout_identity(PROJECT_ROOT)}
    save_evaluation(frozen, output / "freeze.json")
    store, rows = ReportStore(output / "reports"), []
    for source in sources:
        for case in specifications:
            app, document, config, registry, environ = project_inputs(
                case["project"], case["fault"])
            task = task_inputs(case["project"], case["task"])[0]
            draft = fixture_draft(case)
            validator = task_validator(task, load_openapi(document), config, registry)
            if source == "fake":
                planner = FakeLLM(draft.scenario().model_dump_json())
            else:
                def response(request, content=draft.model_dump_json()):
                    return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                        "message": {"content": content}}], "usage": {
                            "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})

                planner = OpenAICompatiblePlanner(ModelSettings(model="offline-portability",
                    base_url="https://offline.invalid/v1", api_key="synthetic-fixture-key"),
                    httpx.MockTransport(response), profile="generic")
            report = await Workflow(document, config, registry, planner, store,
                httpx.ASGITransport(app, raise_app_exceptions=False), environ,
                user_task=task.user_task, intent_validator=validator).run()
            plan = capture_plan(draft.scenario())
            row = {**case, "source": source, "run_id": report.run_id,
                   "plan": plan.model_dump(mode="json"), "grade": grade(case, task, report, plan),
                   "report_sha256": hashlib.sha256(
                       (store.root / report.run_id / "report.json").read_bytes()).hexdigest(),
                   "model_calls": report.model_call.calls if report.model_call else 0,
                   "usage_kind": "SIMULATED" if source == "mock_transport" else "NOT_APPLICABLE"}
            save_evaluation(row, output / (source + "-" + case["case_id"] + ".json"))
            rows.append(row)
    if checkout_identity(PROJECT_ROOT) != frozen["git"]:
        raise ValueError("Source changed during portability checks")
    result = summarize(rows)
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(
        "# 离线跨项目机制验收\n\n固定响应验证，不计为真实模型或独立评测成绩。\n\n"
        + json.dumps({k: v for k, v in result.items() if k != "results"},
                     ensure_ascii=False, indent=2)
        + "\n", encoding="utf-8")
    seal(output)
    verify_portability(output)
    return result


def verify_portability(directory: Path):
    """Regrade sealed snapshots without instantiating targets or making model requests."""
    if read_json(directory / "archive.json").get("files") != inventory(directory):
        raise ValueError("Portability archive changed")
    frozen, result = read_json(directory / "freeze.json"), read_json(directory / "results.json")
    if frozen["schema_version"] != "agenttest.portability.freeze.v1":
        raise ValueError("Unknown portability protocol")
    if (not frozen["sources"] or len(set(frozen["sources"])) != len(frozen["sources"])
            or not set(frozen["sources"]) <= {"fake", "mock_transport"}
            or not frozen["cases"]
            or len({c["case_id"] for c in frozen["cases"]}) != len(frozen["cases"])
            or any(re.fullmatch(r"G[0-9]{3}", c["case_id"]) is None for c in frozen["cases"])):
        raise ValueError("Invalid portability source or case identity")
    expected = [(s, c) for s in frozen["sources"] for c in frozen["cases"]]
    if len(expected) != len(result["results"]):
        raise ValueError("Incomplete portability denominator")
    store, run_ids = ReportStore(directory / "reports"), set()
    for (source, case), row in zip(expected, result["results"]):
        if (source not in {"fake", "mock_transport"} or row["source"] != source
                or any(row[k] != v for k, v in case.items()) or row["run_id"] in run_ids
                or read_json(directory / (source + "-" + case["case_id"] + ".json")) != row):
            raise ValueError("Portability row identity mismatch")
        run_ids.add(row["run_id"])
        report = store.read(row["run_id"])
        path = store.root / row["run_id"] / "report.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["report_sha256"]:
            raise ValueError("Portability report changed")
        task = TaskContract.model_validate_json(json.dumps(
            frozen["tasks"][case["project"] + "/" + case["task"]]))
        plan = PlanEvidence.model_validate(row["plan"])
        if row["grade"] != grade(case, task, report, plan):
            raise ValueError("Portability regrading mismatch")
        if (row["model_calls"] != (report.model_call.calls if report.model_call else 0)
                or row["model_calls"] != int(source == "mock_transport")
                or row["usage_kind"] != (
                    "SIMULATED" if source == "mock_transport" else "NOT_APPLICABLE")):
            raise ValueError("Portability source mismatch")
    if result != summarize(result["results"]):
        raise ValueError("Portability summary mismatch")
    return {"archive_verified": True, "rescored_cases": len(expected), "real_model_calls": 0}
