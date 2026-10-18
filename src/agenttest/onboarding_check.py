"""Two-domain configuration measurements: file/field counts, never invented human timings."""

import json
from pathlib import Path

import httpx

from agenttest.contracts import load_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.eval_v2.archive import inventory, read_json, seal
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.onboarding import prepare_project, prepare_task, write_draft
from agenttest.portability_demo import project_inputs, task_inputs
from agenttest.project import check_project
from agenttest.report import ReportStore, fingerprint
from agenttest.task_contract import TaskContract, task_validator


def changed_leaves(before, after) -> int:
    if isinstance(before, dict) and isinstance(after, dict):
        return sum(changed_leaves(before.get(key), after.get(key)) for key in before.keys() | after)
    if isinstance(before, list) and isinstance(after, list):
        return sum(changed_leaves(before[i] if i < len(before) else None,
                                  after[i] if i < len(after) else None)
                   for i in range(max(len(before), len(after))))
    return int(type(before) is not type(after) or before != after)


async def check_onboarding(output: Path) -> dict:
    from agenttest.workflow import FakeLLM, Workflow

    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    identity = checkout_identity(PROJECT_ROOT)
    frozen = {"git": identity, "domains": ["courses", "tickets"], "source": "fake_asgi",
              "review_simulation": "DEVELOPER_FIXTURE_ONLY", "human_timing": None}
    save_evaluation(frozen, output / "freeze.json")
    rows = []
    for domain in frozen["domains"]:
        directory = output / domain
        directory.mkdir()
        app, document, target, registry, environ = project_inputs(domain)
        old_task, draft = task_inputs(domain, "read")
        files = {"openapi.json": document, "target.json": target.model_dump_json().encode(),
            "policies.json": json.dumps([p.model_dump() for p in registry.definitions()]).encode(),
            "task.json": old_task.model_dump_json().encode()}
        for filename, raw in files.items():
            (directory / filename).write_bytes(raw)
        manifest = {"schema_version": "agenttest.project.v1", "contract": "openapi.json",
            "target": "target.json", "policies": "policies.json", "task_contract": "task.json"}
        project = directory / "project.json"
        save_evaluation(manifest, project)
        first = check_project(project, environ)
        project_draft = prepare_project(load_openapi(document))
        write_draft(project_draft, directory / "project.draft.json")
        new_title = "接入复用课程" if domain == "courses" else "接入复用工单"
        data = old_task.model_dump(mode="json")
        for step in data["requirements"]["steps"]:
            for arg in step["arguments"]:
                if arg["location"] == "body" and arg["name"] in {"name", "subject"}:
                    old_title = arg["value"]
                    arg["value"] = new_title
                    data["user_task"] = data["user_task"].replace(old_title, new_title)
        for step in draft.steps:
            for arg in step.arguments:
                if arg.location == "body" and arg.name in {"name", "subject"}:
                    arg.value = new_title
        pending = prepare_task(project, data["user_task"], environ)
        write_draft(pending, directory / "task.draft.json")
        # This is a developer fixture completing a draft, never an independent human review.
        new_task = TaskContract.model_validate_json(json.dumps(data))
        before = {name: fingerprint((directory / name).read_bytes().hex()) for name in files}
        (directory / "task.json").write_text(new_task.model_dump_json(), encoding="utf-8")
        followup = check_project(project, environ)
        after = {name: fingerprint((directory / name).read_bytes().hex()) for name in files}
        planner = FakeLLM(draft.scenario().model_dump_json())
        validator = task_validator(new_task, load_openapi(document), target, registry)
        report = await Workflow(document, target, registry, planner,
            ReportStore(directory / "reports"), httpx.ASGITransport(app), environ,
            user_task=new_task.user_task,
            intent_validator=validator).run()
        row = {"domain": domain, "contract_operations": len(project_draft.operations),
            "initial_configuration_ready": first.ready,
            "initial_pending_review_slots": [slot.field for slot in project_draft.missing],
            "followup_configuration_ready": followup.ready,
            "followup_manual_requirement_groups": [slot.field for slot in pending.missing],
            "hash_fields_without_task_draft": 3, "hash_fields_to_copy_with_task_draft": 0,
            "core_input_files": len(files), "reused_core_input_files": sum(
                before[name] == after[name] for name in files),
            "changed_task_json_leaves": changed_leaves(old_task.model_dump(mode="json"), data),
            "draft_executable": pending.execution_authorized,
            "task_draft_rejected_by_check_project": not check_project(
                directory / "task.draft.json", environ).ready,
            "followup_fake_outcome": report.outcome.value, "run_id": report.run_id,
            "source_report_sha256": fingerprint(report.model_dump()),
            "asgi_requests": sum(len(step.attempts) for step in report.steps)}
        rows.append(row)
        save_evaluation(row, directory / "measurement.json")
    if checkout_identity(PROJECT_ROOT) != identity:
        raise ValueError("Onboarding measurement source changed")
    result = {"schema_version": "agenttest.onboarding-measurement.v1",
        "classification": "STATIC_COUNTS_AND_FAKE_EXECUTION_ONLY", "human_reviewed": 0,
        "human_timing_seconds": None, "time_saved_seconds": None,
        "real_model_calls": 0, "real_business_requests": 0, "database_writes": 0,
        "review_simulation": frozen["review_simulation"], "domains": rows,
        "verified": all(row["initial_configuration_ready"] and row["followup_configuration_ready"]
            and row["reused_core_input_files"] == 3 and row["changed_task_json_leaves"] == 2
            and row["task_draft_rejected_by_check_project"]
            and row["followup_fake_outcome"] == "PASS" for row in rows)}
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text("# 两域配置复用核对\n\n" + json.dumps(
        result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    seal(output)
    verify_onboarding(output)
    return result


def verify_onboarding(output: Path) -> dict:
    if read_json(output / "archive.json")["files"] != inventory(output):
        raise ValueError("Onboarding archive changed")
    result = read_json(output / "results.json")
    for row in result["domains"]:
        report = ReportStore(output / row["domain"] / "reports").read(row["run_id"])
        if (report.outcome.value != row["followup_fake_outcome"] or
                fingerprint(report.model_dump()) != row["source_report_sha256"]):
            raise ValueError("Onboarding report changed")
    return {"archive_verified": True, "domains": len(result["domains"]), "new_model_calls": 0}
