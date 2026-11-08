"""Fifth isolated read-only loopback fixture, built for the F4 recheck only.

The project, audience, labels, paths and payloads are new relative to rounds three and
four. The three proposals replace F4's broken pairing: every pending expectation is
justified by the operator's own words, and no step asks for an action the text omits.
Answers live in ``tasks()`` and never enter the product runtime context.
"""

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PORT = 19094
ALIASES = ("warden", "registrar", "clerk", "overseer")
FACTS = {
    "openRegister": (
        "/registers/{registerId}",
        "登记簿",
        "warden",
        {"id": 31, "entries": [{"entryId": 58, "seal": "T-77"}], "status": "open"},
    ),
    "readEntry": (
        "/entries/{entryId}",
        "条目",
        "registrar",
        {"entryId": 58, "label": "draft", "seal": "T-77"},
    ),
    "readVoucher": ("/vouchers/{voucherId}", "凭证", "clerk", {"id": 41, "amount": 12}),
    "readRoster": ("/rosters/{rosterId}", "名册", "clerk", {"id": 8, "name": "delta"}),
    "readBeacon": ("/beacons", "信标", None, {"beacon": "steady"}),
    "checkDock": ("/docks", "码头状态", None, {"dock": "d2-clear"}),
}
CORRECT = {"registerId": 31, "entryId": 58, "voucherId": 41, "rosterId": 8}
INTEGERS = ("registerId", "entryId", "voucherId", "rosterId")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def resolve(path, query, role):
    """Pure routing oracle shared by the target and its preflight, never by the scorer."""
    for op, (template, _, allowed_role, body) in FACTS.items():
        pattern = "^" + re.sub(r"\{[^}]+\}", r"([^/]+)", template) + "$"
        match = re.match(pattern, path)
        if not match:
            continue
        names = re.findall(r"\{([^}]+)\}", template)
        values = dict(zip(names, match.groups()))
        for key in INTEGERS:
            if key in values:
                try:
                    values[key] = int(values[key])
                except ValueError:
                    return op, 422, {"error": "invalid fixture identifier"}, {}, {}
        params = {
            key: entries[0] if len(entries) == 1 else entries for key, entries in query.items()
        }
        if role != allowed_role:
            return (
                op,
                401 if role is None else 403,
                {"error": "fixture access denied"},
                values,
                params,
            )
        if any(CORRECT[key] != value for key, value in values.items()):
            return op, 404, {"error": "fixture resource missing"}, values, params
        return op, 200, deepcopy(body), values, params
    return None, 404, {"error": "fixture route missing"}, {}, {}


def create_fixture(folder, tokens):
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    lock = Lock()
    state = sha(encoded(FACTS))
    reverse = {value: key for key, value in tokens.items()}

    @app.get("/__state")
    def status():
        return {"state_sha256": state, "read_only": True}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
    async def target(request: Request, path: str):
        authorization = request.headers.get("authorization")
        role = (
            None
            if authorization is None
            else reverse.get(authorization.removeprefix("Bearer "), "UNRECOGNIZED")
        )
        parsed = urlsplit(str(request.url))
        operation, status_code, body, values, query = resolve(
            parsed.path, parse_qs(parsed.query, keep_blank_values=True), role
        )
        if request.method != "GET":
            status_code, body = 405, {"error": "fixture is read only"}
        response = JSONResponse(body, status_code=status_code)
        active = json.loads((folder / "active.json").read_text(encoding="utf-8"))
        with lock:
            journal = folder / "target-audit.jsonl"
            sequence = 1 + sum(1 for _ in journal.open(encoding="utf-8")) if journal.exists() else 1
            row = dict(
                sequence=sequence,
                position=active["id"],
                config=active.get("config"),
                operation_id=operation,
                method=request.method,
                path=parsed.path,
                role=role,
                values={
                    key: value
                    for key, value in dict(path_parameters=values, query=query).items()
                    if value
                },
                status=status_code,
                response=body,
                response_sha256=sha(response.body),
                state_sha256=state,
            )
            with journal.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        return response

    return app


def configs():
    """Three operator-approved read-only projects, one per recheck requirement."""
    groups = dict(
        chain=["openRegister", "readEntry", "checkDock"],
        partial=["readEntry", "readVoucher"],
        review=["openRegister", "readRoster", "readBeacon", "checkDock"],
    )
    fields = {
        "openRegister": [("$.entries[0].entryId", "exists", None)],
        "readEntry": [("$.label", "equals", "draft")],
    }
    args = dict(
        openRegister=[dict(location="path_parameters", name="registerId", value=31)],
        readEntry=[dict(location="path_parameters", name="entryId", value=58)],
        readVoucher=[dict(location="path_parameters", name="voucherId", value=41)],
        readRoster=[dict(location="path_parameters", name="rosterId", value=8)],
    )
    result = {}
    for key, ids in groups.items():
        paths, operations = {}, []
        for op in ids:
            path, label, role, _ = FACTS[op]
            parameters = [
                dict(
                    name=name,
                    **{"in": "path"},
                    required=True,
                    schema=dict(type="integer" if name in INTEGERS else "string"),
                )
                for name in re.findall(r"\{([^}]+)\}", path)
            ]
            paths[path] = dict(
                get=dict(
                    operationId=op,
                    summary=label,
                    security=[{"fixtureBearer": []}] if role else [],
                    parameters=parameters,
                    responses={"200": {"description": "Isolated fixture"}},
                )
            )
            operations.append(
                dict(
                    operation_id=op,
                    alias=label,
                    role=role,
                    statuses=[200],
                    enabled=True,
                    arguments=args.get(op, []),
                    checks=[
                        dict(select=s, operator=o, expected=e) for s, o, e in fields.get(op, [])
                    ],
                    check_source="Operator-approved immutable read-only fixture, F4 recheck.",
                )
            )
        result[key] = dict(
            name="F4 复验隔离读取 " + key,
            document=json.dumps(
                dict(
                    openapi="3.0.3",
                    info=dict(title=key, version="1"),
                    paths=paths,
                    components=dict(
                        securitySchemes=dict(fixtureBearer=dict(type="http", scheme="bearer"))
                    ),
                ),
                ensure_ascii=False,
            ),
            confirmed=True,
            generic=dict(
                base_url=f"http://127.0.0.1:{PORT}",
                isolated_confirmed=True,
                allow_mutations=False,
                max_requests=8,
                run_timeout=180,
                read_timeout=5,
                assisted=True,
                roles=[
                    dict(alias=alias, bearer_env="AGENTTEST_TARGET_F4_" + alias.upper() + "_TOKEN")
                    for alias in ALIASES
                ],
                operations=operations,
            ),
        )
    return result


def scope(key):
    """Approved operation scope for the rubric lint, including the fixture's own labels."""
    catalog = configs()[key]
    return {
        op["operation_id"]: dict(
            role=op["role"],
            labels=op["alias"],
            checks=[
                (check["select"], check["operator"], check["expected"]) for check in op["checks"]
            ],
        )
        for op in catalog["generic"]["operations"]
    }


def tasks(plan):
    """Keep all three proposal texts byte-for-byte; answers remain evaluation side only."""
    text = Path(plan).read_text(encoding="utf-8")
    requests = dict(re.findall(r"^\| (G[1-3]) \| (.*?) \|", text, re.M))
    assert len(requests) == 3

    def step(op, values=None, fields=()):
        return dict(
            operation=op,
            role=FACTS[op][2],
            values=values or {},
            status=200,
            fields=[dict(select=s, operator=o, expected=e) for s, o, e in fields],
        )

    def permission(op, **extra):
        return dict(kind="permission", operation=op, role="overseer", source="overseer", **extra)

    specs = {
        "G1": (
            "chain",
            [
                step("openRegister", {"path_parameters": {"registerId": 31}}),
                step(
                    "readEntry",
                    {"path_parameters": {"entryId": 58}},
                    [("$.label", "equals", "draft")],
                ),
                step("checkDock"),
            ],
            [],
            [0, 1, 2],
            [
                dict(
                    producer="openRegister",
                    consumer="readEntry",
                    source_role="warden",
                    role="registrar",
                    location="path_parameters",
                    name="entryId",
                    selector="$.entries[0].entryId",
                )
            ],
        ),
        "G2": (
            "partial",
            [
                step("readEntry", {"path_parameters": {"entryId": 58}}),
                step("readVoucher", {"path_parameters": {"voucherId": 41}}),
            ],
            [
                dict(
                    kind="content",
                    operation="readEntry",
                    role="registrar",
                    selector="$.seal",
                    operator="equals",
                    value="T-77",
                    source="$.seal",
                    block_all_roles=True,
                )
            ],
            [0, 1],
            [],
        ),
        "G3": (
            "review",
            [step("readBeacon"), step("checkDock"), step("checkDock")],
            [permission("openRegister"), permission("readRoster", block_all_roles=True)],
            [0, 1, 2],
            [],
        ),
    }
    return [
        dict(
            id=key,
            request=requests[key],
            config=cfg,
            category=key,
            rubric=dict(steps=steps, gaps=gaps, order=order, bindings=bindings),
        )
        for key, (cfg, steps, gaps, order, bindings) in specs.items()
    ]
