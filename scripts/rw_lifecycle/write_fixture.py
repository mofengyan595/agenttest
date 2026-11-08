"""Isolated, resettable read-write target for lifecycle preparation; loopback only.

Nothing here may be pointed at a real service. The fixture keeps every resource inside a
per-run namespace, records an audit row with the state hash before and after each request,
and can inject faults so compensation and cleanup can be scored deterministically. The
reset and fault controls require a private token, so an ordinary test run cannot wipe or
bend the target.
"""

import hashlib
import json
import re
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

PORT = 19097
ALIASES = ("author", "editor", "auditor", "outsider")
WRITE_ROLES = ("author", "editor")
READ_ROLES = ("author", "editor", "auditor")
ITEM_PATH = re.compile(r"^/items/([A-Za-z0-9_-]{1,64})$")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


class Store:
    """All mutable state in one lock; every mutation bumps the version and the hash."""

    def __init__(self, reset_token, tokens):
        self.lock = threading.RLock()
        self.reset_token = reset_token
        self.identities = {token: alias for alias, token in tokens.items()}
        self.namespace = "unset"
        self.items = {}
        self.idempotency = {}
        self.faults = {}
        self.audit = []
        self.next_id = 1

    def state(self):
        with self.lock:
            live = {key: value for key, value in self.items.items() if not value["deleted"]}
            return sha(encoded(dict(namespace=self.namespace, items=live)))

    def reset(self):
        with self.lock:
            self.items.clear()
            self.idempotency.clear()
            self.faults.clear()
            self.audit.clear()
            self.next_id = 1
            self.namespace = "unset"

    def record(
        self, request, operation, role, status, before, body, *, item=None, replay=False, fault=None
    ):
        with self.lock:
            self.audit.append(
                dict(
                    sequence=len(self.audit) + 1,
                    operation_id=operation,
                    method=request.method,
                    path=urlsplit(str(request.url)).path,
                    role=role,
                    namespace=self.namespace,
                    item_id=item,
                    status=status,
                    request_sha256=sha(encoded(body)),
                    state_before=before,
                    state_after=self.state(),
                    idempotent_replay=replay,
                    fault=fault,
                )
            )


def create_fixture(reset_token, tokens):
    store = Store(reset_token, tokens)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    def role_of(request):
        authorization = request.headers.get("authorization")
        if authorization is None:
            return None
        return store.identities.get(authorization.removeprefix("Bearer "), "UNRECOGNIZED")

    def control(request):
        return request.headers.get("x-reset-token") == store.reset_token

    def lookup(identifier):
        with store.lock:
            item = store.items.get(identifier)
            if item is None or item["deleted"] or item["namespace"] != store.namespace:
                return None
            return item

    @app.get("/__state")
    def state():
        with store.lock:
            return {
                "state_sha256": store.state(),
                "namespace": store.namespace,
                "items": len([i for i in store.items.values() if not i["deleted"]]),
                "audit": len(store.audit),
                "read_only": False,
                "isolated": True,
                "writable": True,
            }

    @app.get("/__audit")
    def audit():
        with store.lock:
            return {"rows": list(store.audit)}

    @app.post("/__reset")
    def reset(request: Request):
        if not control(request):
            return JSONResponse({"error": "control token required"}, status_code=403)
        store.reset()
        return {"reset": True}

    @app.post("/__namespace")
    async def namespace(request: Request):
        if not control(request):
            return JSONResponse({"error": "control token required"}, status_code=403)
        body = await request.json()
        value = str(body.get("value") or "")
        if not re.fullmatch(r"[a-z0-9-]{4,40}", value):
            return JSONResponse({"error": "namespace required"}, status_code=422)
        with store.lock:
            store.namespace = value
            store.items.clear()
            store.idempotency.clear()
        return {"namespace": value}

    @app.post("/__faults")
    async def faults(request: Request):
        if not control(request):
            return JSONResponse({"error": "control token required"}, status_code=403)
        body = await request.json()
        with store.lock:
            store.faults = {str(key): str(value) for key, value in (body or {}).items()}
        return {"faults": store.faults}

    @app.get("/items")
    def list_items(request: Request):
        role, before = role_of(request), store.state()
        if role not in READ_ROLES:
            store.record(request, "listItems", role, 401 if role is None else 403, before, {})
            return JSONResponse({"error": "denied"}, status_code=401 if role is None else 403)
        with store.lock:
            visible = [
                dict(item)
                for item in store.items.values()
                if not item["deleted"] and item["owner"] == role
            ]
        store.record(request, "listItems", role, 200, before, {})
        return {"items": visible}

    @app.post("/items")
    async def create_item(request: Request):
        role, before = role_of(request), store.state()
        body = await request.json()
        # The realistic transport is the Idempotency-Key header. The product now declares it
        # as a required header parameter, and the body/query spellings remain accepted so an
        # older approved plan keeps working; all three must resolve to one key.
        key = (
            request.headers.get("idempotency-key")
            or (body or {}).get("idempotencyKey")
            or request.query_params.get("idempotency_key")
        )
        if role not in WRITE_ROLES:
            store.record(request, "createItem", role, 401 if role is None else 403, before, body)
            return JSONResponse({"error": "denied"}, status_code=401 if role is None else 403)
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", key):
            store.record(request, "createItem", role, 400, before, body)
            return JSONResponse({"error": "idempotency key required"}, status_code=400)
        name = str(body.get("name") or "")
        if not name:
            store.record(request, "createItem", role, 422, before, body)
            return JSONResponse({"error": "name required"}, status_code=422)
        with store.lock:
            existing = store.idempotency.get((store.namespace, key))
            if existing is not None:
                item = store.items[existing]
                replay = item["name"] == name
                store.record(
                    request,
                    "createItem",
                    role,
                    200 if replay else 409,
                    before,
                    body,
                    item=existing,
                    replay=replay,
                )
                if not replay:
                    return JSONResponse({"error": "idempotency conflict"}, status_code=409)
                return {"id": existing, "replay": True, "name": item["name"]}
            if store.faults.get("createItem") == "before_commit":
                store.record(request, "createItem", role, 500, before, body, fault="before_commit")
                return JSONResponse({"error": "injected before commit"}, status_code=500)
            identifier = "itm-{:04d}".format(store.next_id)
            store.next_id += 1
            store.items[identifier] = dict(
                id=identifier,
                name=name,
                owner=role,
                version=1,
                namespace=store.namespace,
                deleted=False,
            )
            store.idempotency[(store.namespace, key)] = identifier
            after = store.faults.get("createItem") == "after_commit"
            store.record(
                request,
                "createItem",
                role,
                500 if after else 201,
                before,
                body,
                item=identifier,
                fault=store.faults.get("createItem"),
            )
            if after:
                return JSONResponse({"error": "injected after commit"}, status_code=500)
        return JSONResponse({"id": identifier, "name": name, "version": 1}, status_code=201)

    @app.get("/items/{item_id}")
    def get_item(item_id: str, request: Request):
        role, before = role_of(request), store.state()
        item = lookup(item_id)
        if item is None:
            store.record(request, "getItem", role, 404, before, {}, item=item_id)
            return JSONResponse({"error": "missing"}, status_code=404)
        if role not in READ_ROLES or item["owner"] != role:
            store.record(
                request, "getItem", role, 401 if role is None else 403, before, {}, item=item_id
            )
            return JSONResponse({"error": "denied"}, status_code=401 if role is None else 403)
        store.record(request, "getItem", role, 200, before, {}, item=item_id)
        return {key: item[key] for key in ("id", "name", "version", "owner")}

    async def mutate(item_id, request, operation, partial):
        role, before = role_of(request), store.state()
        body = await request.json()
        item = lookup(item_id)
        if item is None:
            store.record(request, operation, role, 404, before, body, item=item_id)
            return JSONResponse({"error": "missing"}, status_code=404)
        if role not in WRITE_ROLES or item["owner"] != role:
            store.record(
                request, operation, role, 401 if role is None else 403, before, body, item=item_id
            )
            return JSONResponse({"error": "denied"}, status_code=401 if role is None else 403)
        name = str(body.get("name") or "")
        if not partial and not name:
            store.record(request, operation, role, 422, before, body, item=item_id)
            return JSONResponse({"error": "name required"}, status_code=422)
        with store.lock:
            if store.faults.get(operation) == "before_commit":
                store.record(
                    request, operation, role, 500, before, body, item=item_id, fault="before_commit"
                )
                return JSONResponse({"error": "injected before commit"}, status_code=500)
            if name:
                item["name"] = name
            item["version"] += 1
            after = store.faults.get(operation) == "after_commit"
            store.record(
                request,
                operation,
                role,
                500 if after else 200,
                before,
                body,
                item=item_id,
                fault=store.faults.get(operation),
            )
            if after:
                return JSONResponse({"error": "injected after commit"}, status_code=500)
        return {"id": item["id"], "name": item["name"], "version": item["version"]}

    @app.patch("/items/{item_id}")
    async def update_item(item_id: str, request: Request):
        return await mutate(item_id, request, "updateItem", True)

    @app.put("/items/{item_id}")
    async def replace_item(item_id: str, request: Request):
        return await mutate(item_id, request, "replaceItem", False)

    @app.delete("/items/{item_id}")
    def delete_item(item_id: str, request: Request):
        role, before = role_of(request), store.state()
        item = lookup(item_id)
        if item is None:
            store.record(request, "deleteItem", role, 404, before, {}, item=item_id)
            return JSONResponse({"error": "missing"}, status_code=404)
        if role not in WRITE_ROLES or item["owner"] != role:
            store.record(
                request, "deleteItem", role, 401 if role is None else 403, before, {}, item=item_id
            )
            return JSONResponse({"error": "denied"}, status_code=401 if role is None else 403)
        with store.lock:
            if store.faults.get("deleteItem") == "before_commit":
                store.record(
                    request,
                    "deleteItem",
                    role,
                    500,
                    before,
                    {},
                    item=item_id,
                    fault="before_commit",
                )
                return JSONResponse({"error": "injected before commit"}, status_code=500)
            item["deleted"] = True
            item["version"] += 1
            after = store.faults.get("deleteItem") == "after_commit"
            store.record(
                request,
                "deleteItem",
                role,
                500 if after else 204,
                before,
                {},
                item=item_id,
                fault=store.faults.get("deleteItem"),
            )
            if after:
                return JSONResponse({"error": "injected after commit"}, status_code=500)
        return Response(status_code=204)

    app.state.store = store
    return app
