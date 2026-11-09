"""First isolated read-write lifecycle acceptance over the approved loopback fixture.

Three positions (T1 lifecycle, T2 idempotency, T3 identity isolation) run through the
product's normal natural-language planning, approval, dynamic binding, executor HTTP and
audit chain with the configured real model. Every verdict is derived from the fixture's
own audit rows and state hashes plus the product's own report, never from a story.

Modes:
    --freeze   record source/config/requirement/scorer hashes and the target initial state
    --dry      deterministic preflight: save the intake project, dump approved policies
    --run      execute the three first positions exactly once, then archive evidence
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

import authority  # noqa: E402
import lifecycle  # noqa: E402
import preflight  # noqa: E402

APPROVED = dict(authority.WRITE_AUTHORITY, operator_approval="GRANTED")
CASES = [
    dict(
        key="T1",
        title="完整生命周期",
        text="创建名为 first 的条目；读取刚创建的该条目；把它的名称改为 renamed；"
             "再用 replaced 整体替换名称；删除该条目；最后再次读取，确认返回 404。",
        expect_methods=["POST", "GET", "PATCH", "PUT", "DELETE", "GET"],
        expect_statuses=[201, 200, 200, 200, 204, 404],
        denied=0, idempotent=False,
    ),
    dict(
        key="T2",
        title="幂等性",
        text="用已批准的幂等键创建名为 first 的条目；再提交一次完全相同的创建请求；"
             "然后用同一个幂等键创建名为 second 的条目；最后删除该条目并回读确认 404。",
        expect_methods=["POST", "POST", "POST", "DELETE", "GET"],
        expect_statuses=[201, 200, 409, 204, 404],
        denied=0, idempotent=True,
    ),
    dict(
        key="T3",
        title="权限隔离",
        text="用 author 身份创建名为 first 的条目并读取；再用只读身份 auditor 读取同一条目；"
             "然后用匿名身份读取该条目；最后由 author 删除条目并回读确认 404。",
        expect_methods=["POST", "GET", "GET", "GET", "DELETE", "GET"],
        expect_statuses=[201, 200, 403, 401, 204, 404],
        denied=2, idempotent=False,
    ),
]


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def git(*args):
    import subprocess
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def model_identity():
    from agenttest.llm_planner import ModelSettings
    settings = ModelSettings.from_env_file(ROOT / ".env")
    return dict(model=settings.model, protocol=settings.protocol,
                base_url=settings.base_url, max_output_tokens=settings.max_output_tokens)


def freeze(folder):
    """Pin every artifact a later verdict depends on, before any request is sent."""
    with preflight.served():
        target = preflight.reset("e2e-freeze-0001")
    data = dict(
        frozen_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        source=dict(head=git("rev-parse", "HEAD"), status=git("status", "--porcelain"),
                    diff_sha256=digest(git("diff")), src_sha256=digest(sorted(
                        (str(path.relative_to(ROOT)),
                         hashlib.sha256(path.read_bytes()).hexdigest())
                        for path in (ROOT / "src/agenttest").rglob("*.py")))),
        requirements=dict(cases=CASES, digest=digest(CASES)),
        authority=dict(declaration=APPROVED, digest=digest(APPROVED),
                       checklist=list(authority.APPROVAL_CHECKLIST)),
        contract=dict(document=json.loads(authority.document()),
                      document_sha256=digest(authority.document()),
                      setup_sha256=digest(authority.setup())),
        scorer=dict(sha256=hashlib.sha256(
            (ROOT / "scripts/rw_lifecycle/lifecycle.py").read_bytes()).hexdigest()),
        model=model_identity(),
        target=dict(base_url=APPROVED["base_url"], initial=target,
                    isolated=True, loopback_only=True),
        budgets=APPROVED["budgets"],
    )
    (folder / "freeze.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print("frozen head", data["source"]["head"][:7], "clean" if not data["source"]["status"]
          else "DIRTY", "model", data["model"]["model"], "initial", target["state_sha256"][:12])
    return data


def service(workspace, tokens):
    from agenttest.goal_server import configured_task_service
    from agenttest.project_intake import IntakeInput

    os.environ.update({"AGENTTEST_TARGET_RW_" + alias.upper() + "_TOKEN": token
                       for alias, token in tokens.items()})
    svc = configured_task_service(workspace, projects={}, model_env=ROOT / ".env",
                                  generic_intake=True)
    svc.environ.update(os.environ)
    saved = svc.intake.save(IntakeInput(
        name="rw-lifecycle-19097", document=authority.document(), confirmed=True,
        generic=authority.setup()), "operator")
    return svc, saved


def policy_summary(svc, project_id):
    project = svc.project(project_id)
    rows = []
    for policy in project.registry.definitions():
        rows.append(dict(
            id=policy.id, operation=policy.operation_id, roles=policy.roles,
            closed=policy.closed_arguments, statuses=policy.assertions.status.codes
            if policy.assertions.status else [], negative=bool(policy.negative),
            rules=[dict(location=rule.location, name=rule.name,
                        selector=rule.source_selector,
                        enum=(rule.literal_schema or {}).get("enum"),
                        source=rule.source_policy_id) for rule in policy.binding_rules]))
    return rows


def dry(folder):
    """Save the project and prove the approved policies can express each case."""
    with preflight.served():
        svc, saved = service(folder / "workspace", preflight.TOKENS)
        rows = policy_summary(svc, saved["id"])
        project = svc.project(saved["id"])
        names = {(row["operation"], tuple(row["roles"]), tuple(row["statuses"]))
                 for row in rows}
        required = {
            "createItem-author": ("createItem", ("author",), (201,)) in names,
            "getItem-owner": ("getItem", ("author",), (200,)) in names,
            "getItem-auditor-403": ("getItem", ("auditor",), (403,)) in names,
            "getItem-anonymous-401": ("getItem", (None,), (401,)) in names,
            "update-author": ("updateItem", ("author",), (200,)) in names,
            "replace-author": ("replaceItem", ("author",), (200,)) in names,
            "delete-author": ("deleteItem", ("author",), (204,)) in names,
        }
        create = next(row for row in rows if row["operation"] == "createItem"
                      and row["roles"] == ["author"] and not row["negative"])
        header = [rule for rule in create["rules"] if rule["location"] == "headers"]
        name_rule = next(rule for rule in create["rules"] if rule["name"] == "name")
        dynamic = [rule for row in rows for rule in row["rules"] if rule["source"]]
        result = dict(
            ready=all(required.values()) and bool(header) and name_rule["enum"] is None,
            checks=required,
            header_rule=header,
            name_is_generated=name_rule["enum"] is None,
            dynamic_rules=len(dynamic),
            target=dict(environment=project.target.environment,
                        scenario=project.target.business_scenario,
                        allow_mutations=project.target.allow_mutations,
                        max_requests=project.target.max_requests,
                        base_url=project.target.base_url),
            policies=rows,
        )
        (folder / "dry.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
        print("dry ready:", result["ready"], "policy rows:", len(rows),
              "header:", [rule["name"] for rule in header],
              "dynamic rules:", len(dynamic), "target:", result["target"])
        if not result["ready"]:
            print(json.dumps(required, ensure_ascii=False))
            raise SystemExit("DRY_PREFLIGHT_NOT_READY")


async def task(svc, project_id, text):
    from agenttest.task_service import TaskConflict

    try:
        row = await svc.submit("operator", project_id, text, "rw-acceptance")
    except TaskConflict as error:
        return None, str(error)
    started = time.monotonic()
    while time.monotonic() - started < 180:
        current = svc.db.get(row["id"], "operator")
        if current["status"] in {"FINISHED", "NEEDS_REVIEW", "INTERRUPTED", "CANCELLED"}:
            return dict(current, elapsed=round(time.monotonic() - started, 2)), None
        await asyncio.sleep(0.25)
    return None, "TASK_TIMEOUT"


def report_of(svc, current):
    run_id = current.get("run_id") or ""
    path = Path(svc.reports.root) / run_id / "report.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def model_calls(report):
    if not report:
        return None
    rows = report.get("model_calls") or report.get("models") or []
    if isinstance(rows, int):
        return rows
    return len(rows)


def checks(case, rows, created_id):
    """Driver-level guarantees that the lifecycle scorer does not express directly."""
    methods = [row["method"] for row in rows]
    statuses = [row["status"] for row in rows]
    keys = [row["idempotency_key"] for row in rows
            if row["operation_id"] == "createItem" and row["idempotency_key"]]
    dynamic = [row for row in rows if created_id and created_id in (row.get("path") or "")]
    result = dict(
        methods=methods, statuses=statuses,
        dynamic_binding_rows=len(dynamic),
        idempotency_keys=len(set(keys)),
        identity_rows=dict(
            author=len([row for row in rows if row["role"] == "author"]),
            auditor=len([row for row in rows if row["role"] == "auditor"]),
            anonymous=len([row for row in rows if row["role"] is None])),
    )
    findings = []
    if methods != case["expect_methods"] or statuses != case["expect_statuses"]:
        findings.append(dict(reason="driver_sequence_mismatch",
                             detail=dict(methods=methods, statuses=statuses)))
    if len(rows) > 12:
        findings.append(dict(reason="driver_request_budget", detail=len(rows)))
    if case["key"] in {"T1", "T3"} and created_id and not dynamic:
        findings.append(dict(reason="driver_no_dynamic_binding", detail=created_id))
    if case["key"] == "T2":
        if result["idempotency_keys"] != 1:
            findings.append(dict(reason="driver_idempotency_key_drift",
                                 detail=result["idempotency_keys"]))
        creates = [row for row in rows if row["operation_id"] == "createItem"]
        if len({row["item_id"] for row in creates if row["status"] in {200, 201}}) != 1:
            findings.append(dict(reason="driver_idempotency_id_drift",
                                 detail=[row["item_id"] for row in creates]))
        if not [row for row in creates if row["status"] == 409]:
            findings.append(dict(reason="driver_conflict_not_observed", detail=0))
    if case["key"] == "T3":
        denials = [row for row in rows if row["status"] in {401, 403}]
        if not any(row["role"] == "auditor" and row["status"] == 403 for row in denials):
            findings.append(dict(reason="driver_auditor_denial_missing", detail=0))
        if not any(row["role"] is None and row["status"] == 401 for row in denials):
            findings.append(dict(reason="driver_anonymous_denial_missing", detail=0))
        changed = [row for row in denials if row["state_before"] != row["state_after"]]
        if changed:
            findings.append(dict(reason="driver_denial_changed_state", detail=len(changed)))
    return dict(checks=result, findings=findings)


def run_case(folder, svc, project_id, case, run_id, initial):
    namespace = f"e2e-{case['key'].lower()}-{run_id}"
    preflight.reset(namespace)
    with httpx.Client(timeout=10, trust_env=False) as client:
        before = client.get(preflight.BASE + "/__state").json()
    current, error = asyncio.run(task(svc, project_id, case["text"]))
    evidence = dict(case=case["key"], title=case["title"], namespace=namespace,
                    text=case["text"], task=error or dict(
                        id=current["id"], status=current["status"], elapsed=current["elapsed"]))
    if error:
        evidence["verdict"] = "NEEDS_REVIEW"
        evidence["findings"] = [dict(reason="driver_task_not_started", detail=error)]
        return evidence
    rows = preflight.audit_rows()
    with httpx.Client(timeout=10, trust_env=False) as client:
        after = client.get(preflight.BASE + "/__state").json()
    report = report_of(svc, current)
    created = next((row["item_id"] for row in rows
                    if row["operation_id"] == "createItem" and row["status"] == 201), None)
    scored = lifecycle.score(dict(
        namespace=namespace, allowed_operations=[op["operation_id"]
                                                 for op in APPROVED["operations"]],
        identity="author", created=1, cleanup=True, denied=case["denied"],
        idempotent=case["idempotent"], compensation=False,
        initial=initial["state_sha256"], terminal=before["state_sha256"]),
        rows)
    extra = checks(case, rows, created)
    findings = [*scored["findings"], *extra["findings"]]
    calls = model_calls(report)
    if calls is not None and calls > 2:
        findings.append(dict(reason="driver_model_call_budget", detail=calls))
    if current["status"] not in {"FINISHED", "NEEDS_REVIEW"}:
        findings.append(dict(reason="driver_task_unfinished", detail=current["status"]))
    blocking = {item["reason"] for item in findings}
    verdict = ("FAIL" if blocking else scored["verdict"])
    evidence.update(
        verdict=verdict, findings=findings, metrics=scored["metrics"],
        checks=extra["checks"], created_id=created,
        state=dict(before=before, after=after,
                   cleanup=after["items"] == before["items"] == 0),
        audit=[{key: row[key] for key in ("sequence", "operation_id", "method", "path",
                                          "role", "status", "item_id", "idempotency_key",
                                          "idempotent_replay", "fault", "state_before",
                                          "state_after", "namespace")} for row in rows],
        product=dict(status=current["status"], reason=current.get("reason"),
                     model_calls=calls,
                     outcome=(report or {}).get("outcome"),
                     report_reason=(report or {}).get("reason"),
                     steps=[dict(id=step.get("id"), operation_id=step.get("operation_id"),
                                 outcome=step.get("outcome"),
                                 attempts=len(step.get("attempts") or []))
                            for step in (report or {}).get("steps", [])],
                     requests=(report or {}).get("requests")),
    )
    return evidence


def run(folder):
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    assert not frozen["source"]["status"], "FREEZE_REQUIRES_CLEAN_TREE"
    if (folder / "result.json").exists():
        raise SystemExit("POSITION_ALREADY_CONSUMED_NO_REPLAY")
    run_id = time.strftime("%m%d-%H%M%S")
    results = []
    with preflight.served():
        initial = preflight.reset("e2e-freeze-0001")
        svc, saved = service(folder / "workspace", preflight.TOKENS)
        for case in CASES:
            evidence = run_case(folder, svc, saved["id"], case, run_id, initial)
            results.append(evidence)
            unauthorized = [finding for finding in evidence["findings"]
                            if finding["reason"] in {"driver_denial_changed_state",
                                                     "wrong_identity_wrote",
                                                     "unapproved_operation",
                                                     "namespace_violation"}]
            unknown = "UNKNOWN" in json.dumps(evidence.get("product", {}), ensure_ascii=False)
            print(f"{case['key']} {evidence['verdict']:<12} "
                  f"statuses={evidence.get('checks', {}).get('statuses')} "
                  f"requests={evidence.get('metrics', {}).get('requests')} "
                  f"findings={[finding['reason'] for finding in evidence['findings']]}")
            if unauthorized or unknown:
                results.append(dict(case="STOPPED", summary="safety stop", evidence=evidence))
                break
    summary = dict(
        run_id=run_id, frozen_head=frozen["source"]["head"], model=frozen["model"],
        target=frozen["target"]["base_url"],
        verdicts={row["case"]: row["verdict"] for row in results},
        requests={row["case"]: row.get("metrics", {}).get("requests") for row in results},
        model_calls={row["case"]: row.get("product", {}).get("model_calls")
                     for row in results},
        elapsed={row["case"]: (row["task"].get("elapsed")
                               if isinstance(row.get("task"), dict) else None)
                 for row in results},
        leftovers={row["case"]: row.get("metrics", {}).get("leftovers") for row in results},
        cleanup={row["case"]: row.get("state", {}).get("cleanup") for row in results},
        unknown=[row["case"] for row in results
                 if "UNKNOWN" in json.dumps(row.get("product", {}), ensure_ascii=False)],
        authority_after_run=authority.WRITE_AUTHORITY["operator_approval"],
        results=results)
    (folder / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("verdicts", "requests", "model_calls",
                                                    "leftovers", "cleanup", "unknown")},
                     ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["freeze", "dry", "run"])
    parser.add_argument("folder", nargs="?", default="runs/rw-acceptance-20261109")
    args = parser.parse_args()
    folder = (ROOT / args.folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    authority.startable(APPROVED)
    if args.mode == "freeze":
        freeze(folder)
    elif args.mode == "dry":
        dry(folder)
    else:
        run(folder)
    assert authority.WRITE_AUTHORITY["operator_approval"] == "NOT_GRANTED"
    try:
        authority.startable()
    except PermissionError as error:
        print("authority restored to NOT_GRANTED:", error)
    else:
        raise AssertionError("WRITE_AUTHORITY_NOT_GRANTED was not restored")


if __name__ == "__main__":
    main()
