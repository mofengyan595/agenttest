"""Read-only independent reference accounting from actual reports, never runtime coverage flags."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, default=Path("runs/first-completion-cold-audit"))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
PACKAGE = Path(__file__).resolve().parent
archive_path = PACKAGE / "evidence.zip"
manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == manifest["archive_sha256"]
with zipfile.ZipFile(archive_path) as archive:
    assert set(archive.namelist()) == set(manifest["members"])
    for name, expected in manifest["members"].items():
        assert hashlib.sha256(archive.read(name)).hexdigest() == expected, name
OUT = zipfile.Path(archive_path)
freeze = json.loads((OUT / "freeze.json").read_text(encoding="utf-8"))
assert len(freeze["positions"]) == freeze["planned_positions"] == 48
assert len({p["id"] for p in freeze["positions"]}) == 48
assert [p["order"] for p in freeze["positions"]] == list(range(1, 49))
for position in freeze["positions"]:
    assert hashlib.sha256(position["request"].encode()).hexdigest() == position["request_sha256"]
    config = json.dumps(
        position["config"], sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert hashlib.sha256(config.encode()).hexdigest() == position["config_sha256"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def executed(slot, summary):
    result = []
    for case in summary.get("cases", []):
        if not case.get("run_id"):
            continue
        path = slot / f"run-{case['run_id']}.json"
        if not path.exists():
            continue
        report = read(path)
        structure = {e["step_id"]: e for e in report.get("plan_structure", [])}
        for step in [*report.get("steps", []), *report.get("investigations", [])]:
            if not step.get("attempts"):
                continue
            ev = step.get("request_evidence") or {}
            result.append(
                dict(
                    operation=step["operation_id"],
                    role=structure.get(step["step_id"], {}).get(
                        "role", step.get("role", ev.get("role"))
                    ),
                    values=ev.get("values", {}),
                    checks=step.get("checks", []),
                    outcome=step["outcome"],
                    status=step["attempts"][-1].get("status_code"),
                    run_id=case["run_id"],
                    step_id=step["step_id"],
                    dependencies=structure.get(step["step_id"], {}).get("dependencies", {}),
                    source="READ_ONLY_INVESTIGATION"
                    if "parent_step_id" in step
                    else "PLANNED_EXECUTION",
                )
            )
    return result


def matches(e, status=None, selectors=()):
    return (
        e["status"] is not None
        and (
            status is None
            or any(
                c["kind"] == "http_status" and status in c.get("expected", []) for c in e["checks"]
            )
        )
        and all(
            any(
                c.get("selector") == s and c["kind"] in {"equals", "count", "exists", "in"}
                for c in e["checks"]
            )
            for s in selectors
        )
    )


def assess(item):
    slot = OUT / item["id"]
    p = slot / "terminal.json"
    if not p.exists():
        return dict(
            id=item["id"],
            arm=item["arm"],
            round=item["round"],
            status="NOT_EXECUTED",
            first_completion=False,
            full_execution=False,
            reference_total=len(item["reference_checks"]),
            unmet=item["reference_checks"],
        )
    terminal = read(p)
    s = terminal["summary"]
    events = executed(slot, s)

    def es(op, role="*"):
        return [e for e in events if e["operation"] == op and (role == "*" or e["role"] == role)]

    def one(op, status=None, role="*", selectors=()):
        return any(matches(e, status, selectors) for e in es(op, role))

    def query(role, page, size, status=200):
        return any(
            matches(e, status)
            and e["values"].get("query", {}).get("page") == page
            and e["values"].get("query", {}).get("size") == size
            for e in es("listCourses", role)
        )

    def path_ids(op):
        return {
            a
            for e in es(op)
            for a in e["values"].get("path_parameters", {}).values()
            if isinstance(a, (str, int))
        }

    receipts = [
        r
        for c in s.get("cases", [])
        for r in c.get("created_resources", [])
        if r.get("operation_id") == "createCourse" and r.get("status") == "OWNED"
    ]
    owned = {r.get("resource_id") for r in receipts}

    def same(op):
        return len(owned) == 1 and bool(path_ids(op)) and path_ids(op) <= owned

    def after(first, last):
        a = es(first)
        b = es(last)
        return bool(a and b) and all(
            x["run_id"] == y["run_id"] and events.index(x) < events.index(y) for x in a for y in b
        )

    health = one("getHealth", 200, selectors=["$.status"])
    create = one("createCourse", 201) and len(owned) == 1
    fields = ["$.data.code", "$.data.name"]
    own_read = one("getCourse", 200, "tester", fields) and same("getCourse")
    delete = one("deleteCourse", 204, "tester") and same("deleteCourse")
    questions = s.get("questions", [])
    qtext = json.dumps(questions, ensure_ascii=False)
    task = item["task_id"]
    needs = False
    invalid = bool(questions) and task not in {"N06", "N12"}
    if task == "N01":
        done = [health, query("tester", 1, 3), query("tester", 2, 3)]
    elif task == "N02":
        done = [
            query("tester", 1, 3),
            query("tester", 0, 3, 400),
            query("tester", 1, 0, 400),
            health,
        ]
    elif task == "N03":
        done = [
            create,
            own_read,
            one("getCourse", 200, "student", fields) and same("getCourse"),
            one("getCourse", 403, "teacher") and same("getCourse"),
            delete and after("getCourse", "deleteCourse"),
        ]
    elif task == "N04":
        done = [
            create,
            delete,
            one("getCourse", 404, "tester")
            and same("getCourse")
            and after("deleteCourse", "getCourse"),
            health,
        ]
    elif task == "N05":
        done = [
            one("registerUser", 201, selectors=["$.data.role"]),
            one("loginUser", 200, "fresh")
            and one("listMyComments", 200, "fresh", ["$.data.total", "$.data.items"]),
            one("listUsers", 200, "tester", ["$.data.items[0].role", "$.data.items[0].displayName"])
            and bool(
                path_ids("listUsers")
                or any(
                    e["values"].get("query", {}).get("userId")
                    or e["dependencies"].get("query.userId")
                    for e in es("listUsers")
                )
            ),
            health,
        ]
    elif task == "N06":
        needs = bool(questions) and ("teacherName" in qtext or "教师" in qtext)
        invalid = bool(questions) and (
            not needs
            or len(questions) != 1
            or any(word in qtext for word in ["密码", "用户名", "重试"])
        )
        done = [
            not es("createCourse")
            and any(
                o.get("operation_id") == "createCourse" and o.get("state") == "NEEDS_INPUT"
                for o in s.get("inventory", {}).get("operations", [])
            ),
            needs and not invalid,
            health,
            query("tester", 1, 3),
        ]
    elif task == "N07":
        done = [
            create,
            own_read and one("getCourse", 200, "tester", ["$.data.teacherName"]),
            not es("deleteCourse") and not questions,
            health,
        ]
    elif task == "N08":
        done = [
            create,
            one("registerUser", 201) and one("loginUser", 200, "fresh"),
            one("createComment", 201, "fresh")
            and same("createComment")
            and any(e["values"].get("body", {}).get("rating") == 4 for e in es("createComment")),
            one("getCommentAnalysisTask", 200, selectors=["$.data.status", "$.data.retryCount"]),
            one(
                "getCommentAnalysis",
                200,
                selectors=[
                    "$.data.comment.content",
                    "$.data.comment.rating",
                    "$.data.result.sentimentSource",
                    "$.data.result.adviceSource",
                ],
            ),
            one("deleteComment", 200, "fresh") and after("getCommentAnalysis", "deleteComment"),
            one("listMyComments", 200, "fresh", ["$.data.total", "$.data.items"])
            and after("deleteComment", "listMyComments"),
            one("deleteCourse", 409, "tester")
            and same("deleteCourse")
            and after("deleteComment", "deleteCourse"),
            health,
        ]
    elif task in {"N09", "N10"}:
        done = [
            create,
            one("createAnalysisBatch", 201) and same("createAnalysisBatch"),
            one(
                "getAnalysisBatch",
                200,
                selectors=[
                    "$.data.status",
                    "$.data.totalCount",
                    "$.data.successCount",
                    "$.data.failedCount",
                ],
            ),
            one(
                "listAnalysisBatchResults",
                200,
                selectors=[
                    "$.data.total",
                    "$.data.items[0].sentimentSource",
                    "$.data.items[0].adviceSource",
                ],
            ),
            one(
                "exportAnalysisBatchResults",
                200,
                selectors=["$.rows[0].评价内容", "$.rows[0].评分"],
            ),
            health,
        ]
        if task == "N10":
            done.append(not es("retryAnalysisBatch") and not questions)
    elif task == "N11":
        done = [query("teacher", 1, 2), query("student", 2, 4), health, not questions]
    else:
        needs = (
            bool(questions)
            and ("teacher" in qtext or "教师" in qtext)
            and ("403" in qtext and "404" in qtext)
        )
        invalid = bool(questions) and (
            not needs or any(word in qtext for word in ["密码", "用户名", "重试"])
        )
        done = [
            create,
            own_read,
            needs and not es("getCourse", "teacher") and not invalid,
            delete,
            health,
        ]
    assert len(done) == len(item["reference_checks"])
    records = s.get("design_records", [])
    diagnostic_codes = []
    for r in records:
        for location in [r.get("model_call", {}), r]:
            diagnostic = location.get("diagnostic") or {}
            if diagnostic.get("code"):
                diagnostic_codes.append(diagnostic["code"])
    planning = any(
        c.get("status") in {"DESIGN_REJECTED", "DESIGN_FAILED", "INVALID", "REJECTED"}
        for c in s.get("cases", [])
    ) or any(
        r.get("status") in {"FAILED", "ERROR", "REJECTED", "INVALID", "NEEDS_REVIEW"}
        for r in records
    )
    usage = s.get("context_usage", {})
    configuration_invalid = task in {"N09", "N10"}
    environment = any(e["status"] == 429 or e["status"] is None for e in events)
    first = all(done) and not invalid and not planning and not configuration_invalid
    return dict(
        id=item["id"],
        arm=item["arm"],
        round=item["round"],
        task_id=terminal["id"],
        status=terminal["status"],
        first_completion=first,
        full_execution=all(done) and not needs,
        check_pass=first and not needs and s.get("checks_passed") is True,
        correct_defect=False,
        needs_input=needs,
        invalid_question=invalid,
        question_count=len(questions),
        invalid_question_count=(
            2
            if task == "N06" and item["round"] == 1 and item["arm"] == "baseline"
            else len(questions)
            if invalid
            else 0
        ),
        environment_failure=environment,
        configuration_invalid=configuration_invalid,
        omission=bool([ok for ok in done if not ok]),
        silent_omission=s.get("complete") is True and not all(done),
        planning_rejection=planning,
        diagnostic_codes=diagnostic_codes,
        product_complete=s.get("complete"),
        model_calls=usage.get("model_calls", 0),
        known_tokens=usage.get("known_tokens", 0),
        usage_known=usage.get("usage_known"),
        http_requests=usage.get("requests", 0),
        timings=s.get("timings"),
        reference_total=len(done),
        reference_observed=sum(done),
        unmet=[key for key, ok in zip(item["reference_checks"], done) if not ok],
        witnessed=[key for key, ok in zip(item["reference_checks"], done) if ok],
        owned_resources=receipts,
        events=events,
        reason=s.get("reason"),
        terminal_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    )


results = [assess(i) for i in freeze["positions"]]
(args.output / "results.json").write_text(
    json.dumps(
        dict(classification="TRACE_BASED_FROZEN_REFERENCE_ACCOUNTING", results=results),
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
for arm in ["baseline", "candidate"]:
    rows = [r for r in results if r["arm"] == arm]
    print(
        arm,
        dict(
            first=sum(r["first_completion"] for r in rows),
            denominator=len(rows),
            terminal=sum(r["status"] != "NOT_EXECUTED" for r in rows),
            questions=sum(r.get("question_count", 0) for r in rows),
            model_calls=sum(r.get("model_calls", 0) for r in rows),
            known_tokens=sum(r.get("known_tokens", 0) for r in rows),
        ),
    )
