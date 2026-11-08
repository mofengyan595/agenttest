"""New immutable loopback fixtures and operator-owned approvals, without business writes.

Round four uses a separate project, audience, labels and payloads from round three; the
only shared piece is the read-only routing style. Answers live in ``tasks()`` and never
enter the product runtime context.
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

PORT = 19091
ALIASES = ("custodian", "curator", "auditor", "inspector")
FACTS = {
    "readGate": ("/gates", "闸口状态", None, {"gate": "g2-open"}),
    "readKeeper": (
        "/keepers/{keeperId}",
        "值守账号",
        "auditor",
        {"id": 9, "handle": "north-keeper"},
    ),
    "listShelf": (
        "/shelves/{shelfId}",
        "书架",
        "custodian",
        {"id": 12, "cards": [{"ref": "K-208", "slip": "S-31"}], "state": "open"},
    ),
    "showCard": (
        "/cards/{cardRef}",
        "卡片",
        "curator",
        {"ref": "K-208", "title": "散页目录", "marker": "catalogued"},
    ),
    "fetchSlip": ("/slips/{slipCode}", "凭条", "custodian", {"code": "S-31"}),
    "checkPulse": ("/pulse", "运行心跳", None, {"pulse": "steady"}),
    "readLedger": ("/ledgers/{ledgerId}", "台账", "auditor", {"id": 7, "entries": [{"no": 3}]}),
    "fetchNotice": ("/notices", "公告", None, {"notices": ["maintenance"]}),
}
CORRECT = {
    "shelfId": 12,
    "cardRef": "K-208",
    "slipCode": "S-31",
    "ledgerId": 7,
    "keeperId": 9,
}
INTEGERS = ("shelfId", "ledgerId", "keeperId")


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
    """Four operator-approved read-only projects, one per round-four requirement."""
    groups = dict(
        flow=["listShelf", "showCard", "checkPulse"],
        perm=["readGate", "readKeeper", "fetchNotice", "checkPulse"],
        order=["checkPulse", "readLedger"],
        mix=["listShelf", "showCard", "fetchSlip", "fetchNotice"],
    )
    fields = {
        "listShelf": [("$.cards[0].ref", "exists", None)],
        "showCard": [("$.marker", "equals", "catalogued")],
        "fetchSlip": [("$.code", "equals", "S-31")],
    }
    args = dict(
        listShelf=[dict(location="path_parameters", name="shelfId", value=12)],
        showCard=[
            dict(
                location="path_parameters",
                name="cardRef",
                source_operation="listShelf",
                selector="$.cards[0].ref",
            )
        ],
        fetchSlip=[
            dict(
                location="path_parameters",
                name="slipCode",
                source_operation="listShelf",
                selector="$.cards[0].slip",
            )
        ],
        readLedger=[dict(location="path_parameters", name="ledgerId", value=7)],
        readKeeper=[dict(location="path_parameters", name="keeperId", value=9)],
    )
    result = {}
    for key, ids in groups.items():
        paths, operations = {}, []
        for op in ids:
            path, alias, role, _ = FACTS[op]
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
                    summary=alias,
                    security=[{"fixtureBearer": []}] if role else [],
                    parameters=parameters,
                    responses={"200": {"description": "Isolated fixture"}},
                )
            )
            operations.append(
                dict(
                    operation_id=op,
                    alias=alias,
                    role=role,
                    statuses=[200],
                    enabled=True,
                    arguments=args.get(op, []),
                    checks=[
                        dict(select=s, operator=o, expected=e) for s, o, e in fields.get(op, [])
                    ],
                    check_source="Operator-approved immutable read-only fixture, round 4.",
                )
            )
        result[key] = dict(
            name="第四轮隔离读取 " + key,
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
                    dict(alias=alias, bearer_env="AGENTTEST_TARGET_R4_" + alias.upper() + "_TOKEN")
                    for alias in ALIASES
                ],
                operations=operations,
            ),
        )
    return result


def tasks(plan):
    """Keep all four proposal texts byte-for-byte; answers remain evaluation side only."""
    text = Path(plan).read_text(encoding="utf-8")
    requests = dict(re.findall(r"^\| (F[1-4]) \| (.*?) \|", text, re.M))
    assert len(requests) == 4

    def step(op, values=None, fields=()):
        return dict(
            operation=op,
            role=FACTS[op][2],
            values=values or {},
            status=200,
            fields=[dict(select=s, operator=o, expected=e) for s, o, e in fields],
        )

    def permission(op, **extra):
        return dict(kind="permission", operation=op, role="inspector", source="inspector", **extra)

    specs = {
        "F1": (
            "flow",
            [
                step("listShelf", {"path_parameters": {"shelfId": 12}}),
                step(
                    "showCard",
                    {"path_parameters": {"cardRef": "K-208"}},
                    [("$.marker", "equals", "catalogued")],
                ),
                step("checkPulse"),
            ],
            [],
            [0, 1, 2],
            [
                dict(
                    producer="listShelf",
                    consumer="showCard",
                    source_role="custodian",
                    role="curator",
                    location="path_parameters",
                    name="cardRef",
                    selector="$.cards[0].ref",
                )
            ],
        ),
        "F2": (
            "perm",
            [step("fetchNotice"), step("checkPulse"), step("checkPulse")],
            [permission("readGate"), permission("readKeeper", block_all_roles=True)],
            [],
            [],
        ),
        "F3": (
            "order",
            [
                step("checkPulse"),
                step("checkPulse"),
                step("readLedger", {"path_parameters": {"ledgerId": 7}}),
            ],
            [],
            [0, 1, 2],
            [],
        ),
        "F4": (
            "mix",
            [
                step("listShelf", {"path_parameters": {"shelfId": 12}}),
                step(
                    "fetchSlip",
                    {"path_parameters": {"slipCode": "S-31"}},
                    [("$.code", "equals", "S-31")],
                ),
                step("fetchNotice"),
            ],
            [
                dict(
                    kind="content",
                    operation="showCard",
                    role="curator",
                    selector="$.title",
                    operator="equals",
                    value="散页目录",
                    source="$.title",
                    block_all_roles=True,
                )
            ],
            [],
            [
                dict(
                    producer="listShelf",
                    consumer="fetchSlip",
                    source_role="custodian",
                    role="custodian",
                    location="path_parameters",
                    name="slipCode",
                    selector="$.cards[0].slip",
                )
            ],
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
