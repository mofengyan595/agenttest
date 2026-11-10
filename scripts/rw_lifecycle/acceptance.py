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
import contextlib
import hashlib
import importlib
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
import consumption  # noqa: E402
import execution_gate  # noqa: E402
import focus_capture  # noqa: E402
import lifecycle  # noqa: E402
import plan_v2  # noqa: E402
import run_authorization  # noqa: E402
import run_lock  # noqa: E402

PLAN = plan_v2
import preflight  # noqa: E402

APPROVED = dict(authority.WRITE_AUTHORITY, operator_approval="GRANTED")


def planned_cases():
    """Normalise the frozen next-round plan into the driver's case shape."""
    cases = []
    for case in PLAN.FINAL_CASES:
        expect = case["expect"]
        cases.append(dict(
            key=case["key"], title=case["title"], text=case["text"],
            expect_methods=expect["methods"], expect_statuses=expect["statuses"],
            denied=expect["denied"], idempotent=expect["methods"].count("POST") > 1,
            conflict=expect["conflict"], cleanup=expect["cleanup"],
            approved_denials=expect.get("approved_denials", []),
        ))
    return cases


CASES = planned_cases()
# Positions already executed once in an earlier acceptance; never replayed.
CONSUMED_POSITIONS = tuple(getattr(PLAN, "CONSUMED_POSITIONS", ("T1", "T2")))
SELECTED = [case["key"] for case in CASES]
# A code upgrade is admissible only when the executing commit is named at run time.
ACCEPT_CODE_CHANGE = None


def digest(value):
    raw = value if isinstance(value, bytes) else json.dumps(
        value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def git(*args):
    import subprocess
    # Source text is UTF-8 even on a GBK console default; never let decoding hide a diff.
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout.strip()


def model_env_path():
    import os

    return Path(os.environ.get("AGENTTEST_MODEL_ENV") or (ROOT / ".env"))


def model_settings(require_credentials=False):
    """The approved per-call ceiling wins over the model file; the default stays 30 s.

    Offline ``freeze``/``dry`` runs must not depend on a real credential file: configuration is
    validated against an explicit offline placeholder, while ``run`` refuses to start without a
    real model identity.
    """
    import os

    from agenttest.llm_planner import ModelSettings

    os.environ["AGENTTEST_MODEL_TIMEOUT"] = str(APPROVED["model_timeout"])
    path = model_env_path()
    if path.is_file():
        settings = ModelSettings.from_env_file(path)
        if settings.timeout != APPROVED["model_timeout"]:
            raise SystemExit("MODEL_TIMEOUT_NOT_APPLIED")
        return settings
    if require_credentials:
        raise SystemExit("MODEL_CREDENTIALS_MISSING")
    return ModelSettings(model="offline-dry-run", base_url="https://offline.invalid",
                         api_key="offline", timeout=APPROVED["model_timeout"])


def model_identity(require_credentials=False):
    settings = model_settings(require_credentials=require_credentials)
    return dict(model=settings.model, protocol=settings.protocol,
                base_url=settings.base_url, max_output_tokens=settings.max_output_tokens,
                timeout=settings.timeout, max_input_tokens=settings.max_input_tokens,
                credentials="PRESENT" if model_env_path().is_file() else "ABSENT_OFFLINE")


# Every field that decides which service answers and how it is metered. A credential value is
# never part of the identity: only its presence is recorded, and it is never logged.
IDENTITY_FIELDS = ("model", "protocol", "base_url", "timeout", "max_output_tokens",
                   "max_input_tokens")


def normalize_identity(identity):
    """Comparable identity: service address and budget, never credential contents."""
    from urllib.parse import urlsplit

    normalized = {field: identity.get(field) for field in IDENTITY_FIELDS}
    base_url = normalized.get("base_url") or ""
    parts = urlsplit(base_url)
    if parts.scheme and parts.netloc:
        path = parts.path.rstrip("/")
        normalized["base_url"] = f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}"
    else:
        normalized["base_url"] = base_url.rstrip("/")
    normalized["protocol"] = str(normalized.get("protocol") or "").lower()
    for field in ("timeout", "max_output_tokens", "max_input_tokens"):
        if normalized.get(field) is not None:
            normalized[field] = float(normalized[field])
    return normalized


def identity_drift(frozen_identity, current_identity):
    """The concrete fields that changed between the frozen and the current service identity."""
    frozen = normalize_identity(frozen_identity or {})
    current = normalize_identity(current_identity or {})
    return [field for field in IDENTITY_FIELDS if frozen.get(field) != current.get(field)]


def cases():
    """The cases this frozen run may execute, in plan order."""
    return [case for case in CASES if case["key"] in SELECTED]


def freeze(folder, selected=None):
    """Pin every artifact a later verdict depends on, before any request is sent."""
    global SELECTED
    if (folder / "freeze.json").exists():
        # A frozen record is the only reference for a later verdict: overwriting it would erase
        # the difference between the approved round and a quiet repair of it.
        raise SystemExit("FREEZE_RECORD_EXISTS_NO_OVERWRITE")
    if selected:
        SELECTED = [case["key"] for case in CASES if case["key"] in set(selected)]
    rubric = PLAN.consistency()
    with preflight.served():
        target = preflight.reset("e2e-freeze-0001")
    data = dict(
        frozen_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        rubric_check=rubric,
        source=dict(head=git("rev-parse", "HEAD"), status=git("status", "--porcelain"),
                    diff_sha256=digest(git("diff")), src_sha256=digest(sorted(
                        (str(path.relative_to(ROOT)),
                         hashlib.sha256(path.read_bytes()).hexdigest())
                        for path in (ROOT / "src/agenttest").rglob("*.py")))),
        requirements=dict(cases=cases(), digest=digest(cases()),
                          consumed_positions=list(CONSUMED_POSITIONS),
                          plan=PLAN.__name__,
                          plan_sha256=hashlib.sha256(
                              Path(PLAN.__file__).read_bytes()).hexdigest(),
                          rubric=PLAN.RUBRIC,
                          dev_cases=[case["key"] for case in PLAN.DEV_CASES],
                          final_cases=[case["key"] for case in PLAN.FINAL_CASES]),
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
    if not cases():
        raise SystemExit("NO_CASE_SELECTED")
    if not rubric["ok"]:
        print(json.dumps(rubric["problems"], ensure_ascii=False))
        raise SystemExit("RUBRIC_CHECK_FAILED")
    print("frozen head", data["source"]["head"][:7], "clean" if not data["source"]["status"]
          else "DIRTY", "model", data["model"]["model"], "initial", target["state_sha256"][:12])
    return data


def service(workspace, tokens):
    from agenttest.goal_server import configured_task_service
    from agenttest.project_intake import IntakeInput

    os.environ.update({"AGENTTEST_TARGET_RW_" + alias.upper() + "_TOKEN": token
                       for alias, token in tokens.items()})
    focus_capture.install()
    model_settings()  # applies the approved per-call ceiling before the service is built
    svc = configured_task_service(workspace, projects={},
                                  model_env=(model_env_path() if model_env_path().is_file()
                                             else None),
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
            "createItem-conflict-409": ("createItem", ("author",), (409,)) in names,
            "getItem-owner": ("getItem", ("author",), (200,)) in names,
            "getItem-auditor-403": ("getItem", ("auditor",), (403,)) in names,
            "getItem-anonymous-401": ("getItem", (None,), (401,)) in names,
            "getItem-absent-404": ("getItem", ("author",), (404,)) in names,
            "update-author": ("updateItem", ("author",), (200,)) in names,
            "updateItem-auditor-403": ("updateItem", ("auditor",), (403,)) in names,
            "updateItem-anonymous-401": ("updateItem", (None,), (401,)) in names,
            "replace-author": ("replaceItem", ("author",), (200,)) in names,
            "delete-author": ("deleteItem", ("author",), (204,)) in names,
        }
        create = next(row for row in rows if row["operation"] == "createItem"
                      and row["roles"] == ["author"] and tuple(row["statuses"]) == (201,))
        conflict = next(row for row in rows if row["operation"] == "createItem"
                        and row["roles"] == ["author"] and tuple(row["statuses"]) == (409,))
        header = [rule for rule in create["rules"] if rule["location"] == "headers"]
        name_rule = next(rule for rule in create["rules"] if rule["name"] == "name")
        conflict_name = next(rule for rule in conflict["rules"] if rule["name"] == "name")
        dynamic = [rule for row in rows for rule in row["rules"] if rule["source"]]
        approved_values = name_rule["enum"]
        result = dict(
            ready=(all(required.values()) and bool(header)
                   and approved_values == ["first", "second"]
                   and conflict_name["enum"] == ["second"]),
            checks=required,
            header_rule=header,
            approved_values=approved_values,
            conflict_values=conflict_name["enum"],
            dynamic_rules=len(dynamic),
            target=dict(environment=project.target.environment,
                        scenario=project.target.business_scenario,
                        allow_mutations=project.target.allow_mutations,
                        max_requests=project.target.max_requests,
                        base_url=project.target.base_url),
            policies=rows,
            budgets=APPROVED["budgets"],
            model_timeout=APPROVED["model_timeout"],
            cases=[case["key"] for case in cases()],
            consumed_positions=list(CONSUMED_POSITIONS),
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

    label = "rw-acceptance-" + hashlib.sha256(text.encode()).hexdigest()[:12]
    try:
        row = await svc.submit("operator", project_id, text, label)
    except TaskConflict as error:
        return None, str(error)
    started = time.monotonic()
    while time.monotonic() - started < APPROVED["budgets"]["max_seconds"] + 5:
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
    """Known model calls from the product report; never inferred from absent fields."""
    if not report:
        return None
    rows = report.get("model_calls") or report.get("models")
    if isinstance(rows, int):
        return rows
    if isinstance(rows, list):
        return len(rows)
    call = report.get("model_call")
    if isinstance(call, dict) and isinstance(call.get("calls"), int) and call["calls"]:
        return call["calls"]
    calls = report.get("planner_calls")
    if isinstance(calls, int) and calls:
        return calls
    usage = report.get("model_usage")
    if isinstance(usage, dict) and isinstance(usage.get("calls"), int) and usage["calls"]:
        return usage["calls"]
    return None


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
        denials=[dict(operation=row["operation_id"], role=row["role"], status=row["status"])
                 for row in rows if row["status"] in {401, 403, 404, 409}],
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
        conflicted = [row for row in creates if row["status"] == 409]
        if not conflicted:
            findings.append(dict(reason="driver_conflict_not_observed", detail=0))
        elif len({row["item_id"] for row in creates}) != 1:
            findings.append(dict(reason="driver_conflict_touched_other_resource",
                                 detail=[row["item_id"] for row in creates]))
    if case["key"].startswith("T3"):
        approved = [entry if isinstance(entry, dict) else
                    dict(operation=entry[0], role=entry[1], status=entry[2])
                    for entry in case["approved_denials"]]
        expected = {(entry["operation"], entry["role"], entry["status"])
                    for entry in approved}
        observed = {(row["operation_id"], row["role"], row["status"])
                    for row in rows if row["status"] in {401, 403}}
        if not expected <= observed:
            findings.append(dict(reason="driver_approved_denial_missing",
                                 detail=sorted(str(item) for item in expected - observed)))
        changed = [row for row in rows if row["status"] in {401, 403}
                   and row["state_before"] != row["state_after"]]
        if changed:
            findings.append(dict(reason="driver_denial_changed_state", detail=len(changed)))
    return dict(checks=result, findings=findings)


def run_case(folder, svc, project_id, case, run_id, initial):
    namespace = f"e2e-{case['key'].lower()}-{run_id}"
    preflight.reset(namespace)
    with httpx.Client(timeout=10, trust_env=False) as client:
        before = client.get(preflight.BASE + "/__state").json()
    focus_capture.take()
    current, error = asyncio.run(task(svc, project_id, case["text"]))
    focus_rows = focus_capture.take()
    evidence = dict(case=case["key"], title=case["title"], namespace=namespace,
                    text=case["text"], task=error or dict(
                        id=current["id"], status=current["status"], elapsed=current["elapsed"]),
                    focus=focus_rows)
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
        idempotent=case["idempotent"], conflict=case["conflict"], compensation=False,
        approved_denials=case["approved_denials"],
        initial=initial["state_sha256"], terminal=before["state_sha256"]),
        rows)
    extra = checks(case, rows, created)
    findings = [*scored["findings"], *extra["findings"]]
    calls = model_calls(report)
    if calls is not None and calls > 2:
        findings.append(dict(reason="driver_model_call_budget", detail=calls))
    elapsed = evidence["task"].get("elapsed") if isinstance(evidence["task"], dict) else None
    if elapsed is not None and elapsed > APPROVED["budgets"]["max_seconds"]:
        findings.append(dict(reason="driver_case_budget_exceeded", detail=elapsed))
    if current["status"] not in {"FINISHED", "NEEDS_REVIEW"}:
        findings.append(dict(reason="driver_task_unfinished", detail=current["status"]))
    blocking = {item["reason"] for item in findings}
    verdict = ("FAIL" if blocking else scored["verdict"])
    if not rows and current["status"] == "NEEDS_REVIEW":
        # A model-service failure before any target request is an infrastructure verdict, not
        # a lifecycle failure; the empty audit must not be scored as one.
        verdict = "NEEDS_REVIEW"
        findings.append(dict(reason="driver_no_target_requests",
                             detail=current.get("reason") or current["status"]))
    report_steps = (report or {}).get("steps") or []
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
                            for step in report_steps],
                     requests=len(rows)),
    )
    return evidence


def current_view(identity, initial=None):
    """The freeze-shaped view of this code, plan and approved scope; no request is sent.

    The frozen comparison always covers the *complete* planned case set. ``--cases`` only
    selects which position this command may execute; it must never shrink the artifact view,
    otherwise a single-position run would report the unselected position as a changed case.
    """
    return execution_gate.snapshot(
        ROOT, plan=PLAN, cases=CASES, consumed=CONSUMED_POSITIONS, approved=APPROVED,
        checklist=authority.APPROVAL_CHECKLIST, document=authority.document(),
        setup=authority.setup(), identity=identity, initial=initial,
        base_url=APPROVED["base_url"])


def guard(folder, *, identity, initial=None, accept_code_change=None, write=True,
          enforce_code_change=True):
    """Refuse an unrecorded drift before any request; always leave an exclusive manifest."""
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    # The service identity is the most specific reason: report it before any artifact check, so a
    # moved address is never buried behind an unrelated missing declaration.
    drift = execution_gate.identity_drift(frozen.get("model"), identity)
    if drift:
        raise SystemExit(f"MODEL_IDENTITY_DRIFT:{','.join(drift)}")
    report = execution_gate.compare(frozen, current_view(identity, initial))
    if report["blocking"]:
        drift = [item["detail"] for item in report["blocking"]
                 if item["reason"] == "MODEL_IDENTITY_DRIFT"]
        if drift:
            raise SystemExit(f"MODEL_IDENTITY_DRIFT:{','.join(drift[0])}")
        reasons = ",".join(item["reason"] for item in report["blocking"])
        raise SystemExit(f"FROZEN_ARTIFACT_DRIFT:{reasons}")
    head = report["code"].get("executing_head") or ""
    if enforce_code_change and report["code"].get("executing_status"):
        # A run must be attributable to a commit: the manifest names a head, and a dirty tree
        # would mean the executed bytes differ from it.
        raise SystemExit("EXECUTION_REQUIRES_CLEAN_TREE")
    if enforce_code_change and report["code"].get("changed") and accept_code_change not in {
            head, head[:7]}:
        raise SystemExit(f"CODE_DRIFT_UNRECORDED:{head[:7]}")
    manifest = None
    if write:
        manifest = execution_gate.write_manifest(folder, execution_gate.manifest_payload(
            report, frozen, accept_code_change=accept_code_change, initial=initial, root=ROOT))
    return report, manifest


def preflight_report(folder):
    """Offline readiness report for the frozen positions; starts only the loopback fixture."""
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    previous = folder / "result.json"
    result = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else None
    with contextlib.ExitStack() as stack:
        try:
            # A preflight also starts the fixture, so it shares the run lock: it can never reset
            # the target while a real attempt is running, and it never touches a position.
            stack.enter_context(run_lock.execution_lock(folder, holder=dict(mode="preflight")))
        except run_lock.RunLocked as error:
            raise SystemExit(str(error)) from None
        stack.enter_context(preflight.served())
        initial = preflight.reset("e2e-freeze-0001")
        identity = model_identity(require_credentials=False)
        report, manifest = guard(folder, identity=identity, initial=initial, write=True,
                                 enforce_code_change=False)
    offline = identity.get("credentials") != "PRESENT"
    keys = list(dict.fromkeys([*(case["key"] for case in cases()), *CONSUMED_POSITIONS]))
    positions = consumption.summary(
        folder, cases=keys, consumed_positions=CONSUMED_POSITIONS)["positions"]
    checklist = execution_gate.case_checklist(
        frozen, report, cases=cases(), consumed=CONSUMED_POSITIONS, result=result,
        manifest=manifest, identity_offline=offline, positions=positions)
    payload = dict(created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), manifest=manifest.name,
                   report=dict(allowed=report["allowed"], blocking=report["blocking"],
                               warnings=report["warnings"], code=report["code"],
                               identity=report["identity"], target=report["target"]),
                   checklist=checklist, model=report["identity"]["current"],
                   initial=report["target"]["current_initial"])
    path = folder / f"preflight-{(report['code'].get('executing_head') or 'unknown')[:7]}.json"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        raise SystemExit("PREFLIGHT_RECORD_EXISTS_NO_OVERWRITE") from None
    for key, case in checklist["cases"].items():
        items = [f"{row['item']}={row['verdict']}" for row in case["items"]]
        print(f"{key} {case['verdict']:<12} items={items}")
    print(json.dumps(dict(verdict=checklist["verdict"], manifest=manifest.name,
                          blocking=report["blocking"], warnings=report["warnings"]),
                     ensure_ascii=False))
    if checklist["verdict"] == "BLOCKED":
        raise SystemExit(3)
    return payload


def only_position():
    """One command may only execute one frozen position, so evidence can never be mixed."""
    keys = list(SELECTED)
    if len(keys) != 1:
        raise SystemExit("ONE_POSITION_PER_COMMAND:" + ",".join(keys))
    return keys[0]


def authorize(folder, args):
    """Operator command: write one immutable permission for one case and commit."""
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    key = only_position()
    head = args.head or execution_gate.source_snapshot(ROOT)["head"]
    if not isinstance(head, str) or len(head) != 40:
        try:
            head = execution_gate.git(ROOT, "rev-parse", head)
        except Exception:
            raise SystemExit(f"UNKNOWN_EXECUTING_HEAD:{head}") from None
    try:
        path = run_authorization.authorize(
            folder, case=key, executing_head=head, frozen=frozen, operator=args.operator,
            allow_model_calls=args.allow_model_calls,
            allow_loopback_writes=args.allow_loopback_writes,
            max_requests=args.max_requests, max_model_calls=args.max_model_calls,
            max_seconds=args.max_seconds, model_timeout=args.model_timeout,
            note=args.note)
    except run_authorization.AuthorizationError as error:
        raise SystemExit(str(error)) from None
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(json.dumps(dict(authorization=path.name, case=payload["case"],
                          executing_head=payload["executing_head"],
                          frozen_sha256=payload["frozen_sha256"][:12],
                          budgets=payload["budgets"],
                          model_timeout=payload["model_timeout"],
                          operator=payload["operator"]), ensure_ascii=False))
    return path


def run(folder):
    frozen = json.loads((folder / "freeze.json").read_text(encoding="utf-8"))
    assert not frozen["source"]["status"], "FREEZE_REQUIRES_CLEAN_TREE"
    # A real run must prove the same service identity the freeze recorded: a matching model name
    # is not enough when the address, protocol or approved budget changed.
    current_identity = model_identity(require_credentials=True)
    drift = execution_gate.identity_drift(frozen.get("model"), current_identity)
    if drift:
        # The service identity is checked before position selection so a moved address is never
        # reported as a case-selection problem.
        raise SystemExit(f"MODEL_IDENTITY_DRIFT:{','.join(drift)}")
    key = only_position()
    if key in CONSUMED_POSITIONS:
        raise SystemExit(f"CONSUMED_POSITION_REFUSED:{key}")
    case = next(item for item in cases() if item["key"] == key)
    before = consumption.status(folder, key, consumed_positions=CONSUMED_POSITIONS)
    if before["status"] != "UNCONSUMED":
        raise SystemExit(consumption.PositionConsumed(key, before["status"]))
    # The frozen declaration is the maximal approved scope, not a permission to execute. A real
    # run needs a separate operator record for this case, this commit and these budgets; the
    # check happens before the fixture, the lock and the position claim.
    frozen_path = folder / "freeze.json"
    head = execution_gate.source_snapshot(ROOT)["head"]
    try:
        authorization = run_authorization.verify(
            folder, case=key, executing_head=head,
            frozen_sha256=run_authorization.digest(frozen_path.read_bytes()),
            budgets=frozen["budgets"],
            model_timeout=frozen["authority"]["declaration"]["model_timeout"])
    except run_authorization.AuthorizationError as error:
        raise SystemExit(str(error)) from None
    # Only after the external record verified may the in-process view be GRANTED.
    approved_view = dict(authority.WRITE_AUTHORITY, operator_approval="GRANTED")
    authority.startable(approved_view)
    run_id = time.strftime("%m%d-%H%M%S")
    results = []
    with contextlib.ExitStack() as stack:
        try:
            # The fixture, its reset, the model calls, the HTTP traffic, the evidence writes and
            # the cleanup all happen under one cross-process lock; a second process is refused
            # immediately and never creates a claim for its own case.
            stack.enter_context(run_lock.execution_lock(folder, holder=dict(
                case=key, head=head, authorization=authorization["authorization_id"])))
        except run_lock.RunLocked as error:
            raise SystemExit(str(error)) from None
        stack.enter_context(preflight.served())
        initial = preflight.reset("e2e-freeze-0001")
        # The consistency gate runs before the first business request and after the fixture is
        # in its frozen initial state, so the manifest names the exact code and target state.
        report, manifest = guard(folder, identity=current_identity, initial=initial,
                                 accept_code_change=ACCEPT_CODE_CHANGE)
        # The atomic claim is taken after the gate passed and before any task is submitted: a
        # crash from here on leaves the position consumed and every execution blocked.
        claimed = consumption.claim(
            folder, key,
            frozen_digest=execution_gate.digest(frozen),
            frozen_head=frozen.get("source", {}).get("head"),
            executing_head=report["code"].get("executing_head"),
            manifest=manifest.name,
            budgets=APPROVED["budgets"],
            identity_digest=execution_gate.digest(
                execution_gate.normalize_identity(current_identity)),
            initial=initial)
        consumption.start(folder, key)
        try:
            svc, saved = service(folder / "workspace", preflight.TOKENS)
            evidence = run_case(folder, svc, saved["id"], case, run_id, initial)
        except BaseException as error:  # noqa: BLE001 - any abnormal end consumes the position
            consumption.interrupt(folder, key, reason=type(error).__name__,
                                  detail=str(error)[:800])
            raise
        requests = evidence.get("metrics", {}).get("requests")
        calls = evidence.get("product", {}).get("model_calls")
        unknown = "UNKNOWN" in json.dumps(evidence.get("product", {}), ensure_ascii=False)
        elapsed = (evidence["task"].get("elapsed")
                   if isinstance(evidence.get("task"), dict) else None)
        try:
            consumption.complete(folder, key, verdict=evidence["verdict"], evidence=evidence,
                                 requests=requests, model_calls=calls, unknown=unknown,
                                 elapsed=elapsed)
        except consumption.PositionWriteFailed as error:
            raise SystemExit(str(error)) from None
        results.append(evidence)
        print(f"{case['key']} {evidence['verdict']:<12} "
              f"statuses={evidence.get('checks', {}).get('statuses')} "
              f"requests={requests} findings={[row['reason'] for row in evidence['findings']]}")
    keys = [item["key"] for item in cases()]
    view = consumption.summary(folder, cases=keys, consumed_positions=CONSUMED_POSITIONS)
    summary = dict(view, run_id=run_id, attempt=claimed["attempt_id"],
                   frozen_head=frozen["source"]["head"], model=frozen["model"],
                   target=frozen["target"]["base_url"],
                   verdicts={key: evidence["verdict"]},
                   requests={key: requests}, model_calls={key: calls},
                   elapsed={key: elapsed}, unknown=[key] if unknown else [],
                   authority_after_run=authority.WRITE_AUTHORITY["operator_approval"],
                   results=results)
    consumption.write_summary(folder, cases=keys, consumed_positions=CONSUMED_POSITIONS)
    print(json.dumps({item: summary[item] for item in ("verdicts", "requests", "model_calls",
                                                       "unknown", "positions")},
                     ensure_ascii=False))
    return summary


def status_report(folder):
    """Read-only inspection: no service, no model call, no target request."""
    keys = list(dict.fromkeys([*(case["key"] for case in CASES), *CONSUMED_POSITIONS]))
    payload = dict(consumption.summary(folder, cases=keys,
                                       consumed_positions=CONSUMED_POSITIONS),
                   plan=PLAN.__name__, consumed_positions=list(CONSUMED_POSITIONS),
                   selected=list(SELECTED))
    path = consumption.write_summary(folder, cases=keys,
                                     consumed_positions=CONSUMED_POSITIONS)
    print(json.dumps(dict(folder=str(folder), summary=path.name,
                          positions={key: row["status"]
                                     for key, row in payload["positions"].items()},
                          runnable=payload["runnable"]), ensure_ascii=False))
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["freeze", "dry", "preflight", "run", "status",
                                         "authorize"])
    parser.add_argument("folder", nargs="?", default="runs/rw-acceptance-20261109")
    parser.add_argument("--plan", default="plan_v2",
                        choices=["plan_v2", "plan_v3", "plan_v4"])
    parser.add_argument("--cases", default="",
                        help="comma-separated keys; consumed positions are refused")
    parser.add_argument("--accept-code-change", default=None,
                        help="executing head (full or 7 chars) whose source difference is audited")
    parser.add_argument("--operator", default=None,
                        help="authorize: name of the human issuing this permission")
    parser.add_argument("--head", default=None,
                        help="authorize: executing commit; defaults to the current HEAD")
    parser.add_argument("--allow-model-calls", action="store_true",
                        help="authorize: explicit consent to real model calls for this case")
    parser.add_argument("--allow-loopback-writes", action="store_true",
                        help="authorize: explicit consent to isolated loopback writes")
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--max-model-calls", type=int, default=None)
    parser.add_argument("--max-seconds", type=int, default=None)
    parser.add_argument("--model-timeout", type=int, default=None)
    parser.add_argument("--note", default=None)
    args = parser.parse_args()
    global PLAN, CASES, CONSUMED_POSITIONS, SELECTED, ACCEPT_CODE_CHANGE
    PLAN = importlib.import_module(args.plan)
    CASES = planned_cases()
    CONSUMED_POSITIONS = tuple(getattr(PLAN, "CONSUMED_POSITIONS", ("T1", "T2")))
    SELECTED = [case["key"] for case in CASES]
    ACCEPT_CODE_CHANGE = args.accept_code_change
    folder = (ROOT / args.folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    selected = [key.strip() for key in args.cases.split(",") if key.strip()]
    if selected:
        known = {case["key"] for case in planned_cases()}
        unknown = sorted(set(selected) - known)
        if unknown:
            raise SystemExit(f"UNKNOWN_CASES:{','.join(unknown)}")
        consumed = set(CONSUMED_POSITIONS) & set(selected)
        if consumed:
            raise SystemExit(f"CONSUMED_POSITION_REFUSED:{','.join(sorted(consumed))}")
        SELECTED = [case["key"] for case in CASES if case["key"] in set(selected)]
    if args.mode == "freeze":
        freeze(folder, selected)
    elif args.mode == "dry":
        dry(folder)
    elif args.mode == "authorize":
        authorize(folder, args)
    elif args.mode == "preflight":
        preflight_report(folder)
    elif args.mode == "status":
        status_report(folder)
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
