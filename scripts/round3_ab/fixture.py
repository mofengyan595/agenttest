"""New immutable loopback fixtures and operator-owned approvals, without business writes."""

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PORT = 19081
ALIASES = ("archivist", "reviewer", "owner", "reader")
FACTS = {
    "readBuild": ("/build", "构建信息", None, {"build": "fixture-r1"}),
    "readAccount": ("/account", "当前账号", "archivist", {"alias": "archive-reader"}),
    "readIndex": (
        "/indexes/{indexId}",
        "索引",
        "owner",
        {"id": 41, "links": [{"id": 73, "code": "R73"}]},
    ),
    "readEntry": ("/entries/{entryId}", "条目", "reader", {"id": 73, "label": "ready-for-review"}),
    "readReceipt": ("/receipts/{code}", "回执", "owner", {"code": "R73"}),
    "readHealth": ("/health", "健康状态", None, {"status": "ok"}),
    "readTags": ("/tags", "标签", None, {"tags": ["fixture"]}),
    "readPage": ("/pages", "分页结果", None, {"page": 3}),
    "readUnavailable": ("/unavailable/{itemId}", "不可用条目", None, {"id": 58}),
}


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
        for key in ("indexId", "entryId", "itemId"):
            if key in values:
                try:
                    values[key] = int(values[key])
                except ValueError:
                    return op, 422, {"error": "invalid fixture identifier"}, {}, {}
        params = {
            key: entries[0] if len(entries) == 1 else entries for key, entries in query.items()
        }
        if "page" in params:
            try:
                params["page"] = int(params["page"])
            except (ValueError, TypeError):
                return op, 422, {"error": "invalid page"}, values, params
        if role != allowed_role:
            return (
                op,
                401 if role is None else 403,
                {"error": "fixture access denied"},
                values,
                params,
            )
        correct = {"indexId": 41, "entryId": 73, "code": "R73", "itemId": 58}
        if any(correct[key] != value for key, value in values.items()) or (
            op == "readPage" and params != {"page": 3}
        ):
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
    groups = dict(
        meta=["readBuild", "readAccount"],
        flow=["readIndex", "readEntry", "readReceipt", "readHealth"],
        hold=["readBuild", "readAccount", "readTags", "readPage", "readUnavailable"],
        guard=["readBuild", "readAccount", "readIndex", "readEntry", "readReceipt", "readHealth"],
    )
    fields = {
        "readBuild": [("$.build", "equals", "fixture-r1")],
        "readAccount": [("$.alias", "equals", "archive-reader")],
        "readIndex": [("$.links[0].id", "exists", None)],
        "readEntry": [("$.id", "equals", 73)],
        "readReceipt": [("$.code", "equals", "R73")],
    }
    args = dict(
        readIndex=[dict(location="path_parameters", name="indexId", value=41)],
        readEntry=[
            dict(
                location="path_parameters",
                name="entryId",
                source_operation="readIndex",
                selector="$.links[0].id",
            )
        ],
        readReceipt=[
            dict(
                location="path_parameters",
                name="code",
                source_operation="readIndex",
                selector="$.links[0].code",
            )
        ],
        readPage=[dict(location="query", name="page", value=3)],
        readUnavailable=[dict(location="path_parameters", name="itemId", value=58)],
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
                    schema=dict(type="string" if name == "code" else "integer"),
                )
                for name in re.findall(r"\{([^}]+)\}", path)
            ]
            if op == "readPage":
                parameters.append(
                    dict(
                        name="page",
                        **{"in": "query"},
                        required=True,
                        schema=dict(type="integer", minimum=1),
                    )
                )
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
                    enabled=op != "readUnavailable",
                    arguments=args.get(op, []),
                    checks=[
                        dict(select=s, operator=o, expected=e) for s, o, e in fields.get(op, [])
                    ],
                    check_source="Operator-approved immutable read-only fixture, round 3.",
                )
            )
        result[key] = dict(
            name="第三轮隔离读取 " + key,
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
                    dict(alias=alias, bearer_env="AGENTTEST_TARGET_R3_" + alias.upper() + "_TOKEN")
                    for alias in ALIASES
                ],
                operations=operations,
            ),
        )
    return result


def tasks(plan):
    """Keep all twelve proposal texts byte-for-byte; answers remain evaluation side only."""
    text = Path(plan).read_text(encoding="utf-8")
    requests = dict(re.findall(r"^\| ([IDG]\d{2}) \| (.*?) \|", text, re.M))
    assert len(requests) == 12

    def step(op, fields=()):
        role = FACTS[op][2]
        values = dict(
            readIndex={"path_parameters": {"indexId": 41}},
            readEntry={"path_parameters": {"entryId": 73}},
            readReceipt={"path_parameters": {"code": "R73"}},
            readPage={"query": {"page": 3}},
        )
        return dict(
            operation=op,
            role=role,
            values=values.get(op, {}),
            status=200,
            fields=[dict(select=s, operator=o, expected=e) for s, o, e in fields],
        )

    def permission(op="readBuild", **extra):
        return dict(kind="permission", operation=op, role="reviewer", source="reviewer", **extra)

    def binding(consumer, name, selector):
        return dict(
            producer="readIndex",
            consumer=consumer,
            source_role="owner",
            role=FACTS[consumer][2],
            location="path_parameters",
            name=name,
            selector=selector,
        )

    account = step("readAccount", [("$.alias", "equals", "archive-reader")])
    source, health = step("readIndex"), step("readHealth")
    dependency = binding("readEntry", "entryId", "$.links[0].id")
    unavailable = dict(
        kind="unavailable",
        operation="readUnavailable",
        name="itemId",
        value=58,
        source="itemId=58",
        block_all_roles=True,
    )
    specs = {
        "I01": (
            "meta",
            [step("readBuild"), step("readBuild"), account],
            [permission()],
            [0, 1, 2],
            [],
        ),
        "I02": ("meta", [step("readBuild"), account], [permission()], [0, 1], []),
        "I03": ("meta", [step("readBuild"), step("readAccount")], [permission()], [0, 1], []),
        "I04": (
            "meta",
            [step("readBuild", [("$.build", "equals", "fixture-r1")])],
            [permission(), permission("readAccount", block_all_roles=True)],
            [],
            [],
        ),
        "D01": (
            "flow",
            [source, step("readEntry", [("$.id", "equals", 73)]), health],
            [],
            [0, 1, 2],
            [dependency],
        ),
        "D02": (
            "flow",
            [source, step("readReceipt", [("$.code", "equals", "R73")]), health],
            [],
            [0, 1, 2],
            [binding("readReceipt", "code", "$.links[0].code")],
        ),
        "D03": (
            "flow",
            [source, health],
            [
                dict(
                    kind="content",
                    operation="readEntry",
                    role="reader",
                    selector="$.label",
                    operator="equals",
                    value="ready-for-review",
                    source="$.label",
                    block_all_roles=True,
                )
            ],
            [],
            [],
        ),
        "D04": (
            "flow",
            [source, health],
            [
                dict(
                    kind="source",
                    operation="readEntry",
                    role="reader",
                    selector="$.links[1].id",
                    source="$.links[1].id",
                    block_all_roles=True,
                )
            ],
            [],
            [],
        ),
        "G01": (
            "hold",
            [step("readPage"), step("readTags"), step("readTags")],
            [unavailable],
            [],
            [],
        ),
        "G02": (
            "hold",
            [step("readPage"), step("readTags")],
            [permission(block_all_roles=True)],
            [0, 1],
            [],
        ),
        "G03": ("hold", [step("readTags"), step("readPage")], [unavailable], [0, 1], []),
        "G04": (
            "guard",
            [step("readIndex", [("$.links[0].id", "exists", None)]), step("readEntry")],
            [permission(block_all_roles=True)],
            [0, 1],
            [dependency],
        ),
    }
    return [
        dict(
            id=key,
            request=requests[key],
            config=cfg,
            category=key[0],
            rubric=dict(steps=steps, gaps=gaps, order=order, bindings=bindings),
        )
        for key, (cfg, steps, gaps, order, bindings) in specs.items()
    ]
