"""Operator-facing write authority for the isolated lifecycle fixture; no write is implied.

The declaration below is deliberately *not* startable. ``operator_approval`` stays
``NOT_GRANTED`` until a human approves this exact operation list, this exact namespace
rule and this exact cleanup policy. ``startable()`` is the single gate every caller must
pass before any state-changing request is sent, so preparation cannot silently become a
write experiment.
"""

import json

from write_fixture import ALIASES, PORT

WRITE_AUTHORITY = dict(
    environment="isolated",
    base_url=f"http://127.0.0.1:{PORT}",
    allow_mutations=True,
    isolated_confirmed=True,
    operator_approval="NOT_GRANTED",
    namespace_rule="每次运行由 e2e 前缀 + 运行 ID 组成，重置夹具后创建；"
                   "资源只在本命名空间可见，跨命名空间一律 404。",
    idempotency_rule="createItem 必须携带必填 Idempotency-Key 头；同键同体返回 200 与原 id，"
                     "同键异体返回 409，绝不二次创建；不同键创建新资源。",
    ownership_rule="资源归创建者所有；非所有者读取/更新/删除一律 403，匿名 401。",
    cleanup_rule="每个创建都必须配对一次 DELETE，并回读 404 作为清理证据；"
                 "未清理的资源计入 leftovers 并阻止验收。",
    budgets=dict(max_requests=12, max_seconds=120, max_model_calls=2),
    operations=[
        dict(operation_id="createItem", method="POST", path="/items", role="author",
             statuses=[201], requires=["Idempotency-Key"], mutation=True),
        dict(operation_id="getItem", method="GET", path="/items/{itemId}", role="author",
             statuses=[200], mutation=False),
        dict(operation_id="updateItem", method="PATCH", path="/items/{itemId}", role="author",
             statuses=[200], mutation=True),
        dict(operation_id="replaceItem", method="PUT", path="/items/{itemId}", role="author",
             statuses=[200], mutation=True),
        dict(operation_id="deleteItem", method="DELETE", path="/items/{itemId}", role="author",
             statuses=[204], mutation=True),
        dict(operation_id="listItems", method="GET", path="/items", role="author",
             statuses=[200], mutation=False),
    ],
    resource_creations={"createItem": dict(id_selector="$.id",
                                           fresh_resource_basis="每次运行新建一个命名的条目")},
    resource_lifecycles=[dict(create_operation="createItem", delete_operation="deleteItem",
                              path_parameter="itemId", id_selector="$.id",
                              fresh_resource_basis="每次运行新建一个命名的条目")],
)

APPROVAL_CHECKLIST = (
    "批准本文件列出的 6 个操作（POST/GET/PATCH/PUT/DELETE/GET 列表）作为唯一可写范围",
    "确认目标仅为 127.0.0.1 上的隔离夹具，且环境标记为 isolated",
    "确认每次运行的命名空间前缀与重置方式（POST /__namespace + POST /__reset）",
    "确认清理策略：每次创建必须配对 DELETE，并以回读 404 作为清理证据",
    "确认故障注入仅用于补偿测试，且不重放未知结果的写请求",
    "确认预算上限（12 次请求 / 120 秒 / 2 次模型调用）与停止条件",
)


def startable(authority=None):
    """Refuse to send any state-changing request before explicit human approval."""
    authority = authority or WRITE_AUTHORITY
    if authority["operator_approval"] != "GRANTED":
        raise PermissionError("WRITE_AUTHORITY_NOT_GRANTED")
    if authority["environment"] != "isolated" or not authority["isolated_confirmed"]:
        raise PermissionError("ISOLATED_ENVIRONMENT_CONFIRMATION_REQUIRED")
    if not authority["allow_mutations"]:
        raise PermissionError("WRITE_AUTHORIZATION_REQUIRED")
    if not authority["base_url"].startswith("http://127.0.0.1:"):
        raise PermissionError("LOOPBACK_ONLY")
    return True


def document():
    """OpenAPI 3.0.3 for the six operations; the idempotency header is optional metadata."""
    paths = {}
    for operation in WRITE_AUTHORITY["operations"]:
        parameters = [dict(name=name, **{"in": "path"}, required=True,
                           schema=dict(type="string"))
                      for name in ("itemId",) if "{" + name + "}" in operation["path"]]
        if operation["operation_id"] == "createItem":
            parameters.append(dict(name="Idempotency-Key", **{"in": "header"}, required=True,
                                   schema=dict(type="string")))
        responses = {str(operation["statuses"][0]): {"description": "Isolated"}}
        if operation["operation_id"] == "createItem":
            responses["409"] = {"description": "Same idempotency key with a different body"}
        if operation["operation_id"] == "getItem":
            responses["401"] = {"description": "Anonymous is rejected"}
            responses["404"] = {"description": "Deleted item is gone"}
        if operation["operation_id"] == "updateItem":
            responses["401"] = {"description": "Anonymous is rejected"}
            responses["403"] = {"description": "Non-owner is rejected"}
        body = None
        if operation["method"] in {"POST", "PATCH", "PUT"}:
            properties = dict(name=dict(type="string"))
            body = dict(required=True, content={"application/json": dict(
                schema=dict(type="object", properties=properties))})
        entry = dict(operationId=operation["operation_id"], summary=operation["operation_id"],
                     security=[{"fixtureBearer": []}] if operation["role"] else [],
                     parameters=parameters, responses=responses)
        if body:
            entry["requestBody"] = body
        paths.setdefault(operation["path"], {})[operation["method"].lower()] = entry
    return json.dumps(dict(openapi="3.0.3", info=dict(title="rw-lifecycle", version="1"),
                           paths=paths,
                           components=dict(securitySchemes=dict(
                               fixtureBearer=dict(type="http", scheme="bearer")))),
                      ensure_ascii=False)


def setup():
    """GenericSetup-shaped projection of the same declaration, for the existing intake."""
    arguments = {
        # The name is an approved set of generated alternatives, so the model may pick the
        # variant named in the requirement but can never invent a new one.
        "createItem": [dict(location="body", name="name", values=["first", "second"],
                            generated=True),
                       dict(location="headers", name="Idempotency-Key",
                            value="rw-lifecycle-key-0001")],
        "getItem": [dict(location="path_parameters", name="itemId",
                         source_operation="createItem", selector="$.id")],
        "updateItem": [dict(location="path_parameters", name="itemId",
                            source_operation="createItem", selector="$.id"),
                       dict(location="body", name="name", value="renamed")],
        "replaceItem": [dict(location="path_parameters", name="itemId",
                             source_operation="createItem", selector="$.id"),
                        dict(location="body", name="name", value="replaced")],
        "deleteItem": [dict(location="path_parameters", name="itemId",
                            source_operation="createItem", selector="$.id")],
        "listItems": [],
    }
    return dict(
        base_url=WRITE_AUTHORITY["base_url"],
        isolated_confirmed=True,
        allow_mutations=True,
        assisted=True,
        approve_negative_tests=True,
        max_requests=WRITE_AUTHORITY["budgets"]["max_requests"],
        run_timeout=WRITE_AUTHORITY["budgets"]["max_seconds"],
        read_timeout=5,
        roles=[dict(alias=alias, bearer_env="AGENTTEST_TARGET_RW_" + alias.upper() + "_TOKEN")
               for alias in ALIASES],
        operations=[
            dict(operation_id=operation["operation_id"], alias=operation["operation_id"],
                 role=operation["role"], statuses=operation["statuses"], enabled=True,
                 arguments=arguments[operation["operation_id"]],
                 checks=[], check_source="Operator-approved isolated lifecycle fixture.",
                 **({"access_checks": [
                     dict(role="auditor", statuses=[403],
                          rule="只读身份读取所有者条目应返回 403"),
                     dict(role=None, statuses=[401], rule="匿名读取条目应返回 401")]}
                    if operation["operation_id"] == "getItem" else {}),
                 **({"access_checks": [
                     # A mutation may only carry a pre-approved identity denial; the
                     # fixture refuses non-owner and anonymous writes.
                     dict(role="auditor", statuses=[403],
                          rule="非所有者身份写入他人条目应返回 403"),
                     dict(role=None, statuses=[401], rule="匿名写入条目应返回 401")]}
                    if operation["operation_id"] == "updateItem" else {}),
                 **({"expectations": [
                     dict(statuses=[404], rule="删除后回读该条目应返回 404")]}
                    if operation["operation_id"] == "getItem" else {}),
                 **({"expectations": [
                     dict(statuses=[409], rule="同一幂等键、不同名称的创建应返回 409 冲突",
                          arguments=[dict(location="body", name="name", value="second")])]}
                    if operation["operation_id"] == "createItem" else {}),
                 **({"creation": WRITE_AUTHORITY["resource_creations"][
                     operation["operation_id"]]}
                    if operation["operation_id"] in WRITE_AUTHORITY["resource_creations"]
                    else {}))
            for operation in WRITE_AUTHORITY["operations"]
        ],
        resource_lifecycles=WRITE_AUTHORITY["resource_lifecycles"],
    )
