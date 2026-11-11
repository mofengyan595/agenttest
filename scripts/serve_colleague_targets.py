"""Three independent disposable loopback APIs for product development and acceptance.

These are source-controlled fixtures, not the product model's generated runtime code. No production
configuration, external network or user data is used. Credentials come only from env.
"""

import argparse
import os
import secrets
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from jsonschema import Draft7Validator


def schema_object(properties, required=None):
    return dict(type="object", properties=properties,
                required=list(properties) if required is None else required,
                additionalProperties=False)


def response(schema=None, description="documented response", links=None):
    value = dict(description=description)
    if schema:
        value["content"] = {"application/json": dict(schema=schema)}
    if links:
        value["links"] = links
    return value


def operation(identifier, summary, codes, body=None, parameters=None, protected=False):
    value = dict(operationId=identifier, summary=summary, responses=codes)
    if body:
        value["requestBody"] = dict(required=True,
            content={"application/json": dict(schema=body)})
    if parameters:
        value["parameters"] = parameters
    if protected:
        value["security"] = [dict(bearer=[])]
    return value


def links(prefix, parameter, selector, consumers):
    return {f"next_{name}": dict(operationId=prefix + name,
        parameters={parameter: "$response.body#/" + selector}) for name in consumers}


def document(kind):
    text = dict(type="string", minLength=1, maxLength=24)
    identifier = dict(type="string")
    healthy = schema_object(dict(state=dict(type="string", enum=["ready"])))
    paths = {"/availability": {"get": operation("readAvailability", "读取服务可用状态",
        {"200": response(healthy)})}}
    if kind == "parcels":
        meta = schema_object(dict(labels=dict(type="array", minItems=1, maxItems=3,
            items=dict(type="string", enum=["blue", "green", "amber"])),
            options=schema_object(dict(tracked=dict(type="boolean")))))
        body = schema_object(dict(name=text, quantity=dict(type="integer", minimum=1, maximum=3),
                                  meta=meta))
        returned = schema_object(dict(id=identifier, **body["properties"]))
        paths["/parcels"] = {"post": operation("createParcel", "创建专用测试包裹",
            {"201": response(returned, links=links("", "parcelId", "id",
                ["readParcel", "replaceParcel", "changeParcel", "deleteParcel"])),
             "422": response(description="输入不符合文档约束")}, body)}
        parameter = [dict(name="parcelId", **{"in": "path"}, required=True, schema=identifier)]
        paths["/parcels/{parcelId}"] = {
            "get": operation("readParcel", "读取包裹内容", {"200": response(returned),
                "404": response(description="包裹不存在")}, parameters=parameter),
            "put": operation("replaceParcel", "替换包裹内容", {"200": response(returned),
                "404": response(description="包裹不存在"),
                "422": response(description="输入不符合文档约束")}, body, parameter),
            "patch": operation("changeParcel", "更新包裹名称", {"200": response(returned),
                "404": response(description="包裹不存在"),
                "422": response(description="名称不符合文档约束")}, schema_object(dict(name=text)),
                parameter),
            "delete": operation("deleteParcel", "删除本轮创建的包裹", {"204": response(),
                "404": response(description="包裹不存在")}, parameters=parameter)}
    elif kind == "reservations":
        body = schema_object(dict(title=text, slot=schema_object(dict(
            day=dict(type="string", enum=["Monday", "Tuesday"]),
            seats=dict(type="array", minItems=1, maxItems=3,
                       items=dict(type="integer", minimum=1, maximum=6))))))
        returned = schema_object(dict(reservation_id=identifier, **body["properties"]))
        login = schema_object(dict(username=dict(type="string"),
                                   password=dict(type="string", format="password")))
        paths["/session"] = {"post": operation("openSession", "登录测试身份", {"200": response(
            schema_object(dict(access_token=dict(type="string"))))}, login)}
        paths["/reservations"] = {"post": operation("createReservation", "创建测试预约",
            {"201": response(returned, links=links("", "reservationId", "reservation_id",
                                                  ["readReservation", "deleteReservation"])),
             "422": response(description="输入不符合文档约束")}, body, protected=True)}
        parameter = [dict(name="reservationId", **{"in": "path"}, required=True,
                          schema=identifier)]
        paths["/reservations/{reservationId}"] = {
            "get": operation("readReservation", "读取预约详情", {"200": response(returned),
                "403": response(description="不允许访问"), "404": response(description="不存在")},
                parameters=parameter, protected=True),
            "delete": operation("deleteReservation", "删除自己创建的测试预约",
                {"204": response()}, parameters=parameter, protected=True)}
    elif kind == "directory":
        for index in range(44):
            item = schema_object(dict(key=dict(type="integer"),
                                      label=dict(type="string"), active=dict(type="boolean")))
            shape = (schema_object(dict(items=dict(type="array", items=item)))
                     if index % 2 else schema_object(dict(data=item)))
            paths[f"/sections/{index}"] = {"get": operation(f"readSection{index}",
                f"读取第 {index} 个目录分区", {"200": response(shape),
                    "422": response(description="查询参数不符合文档约束")}, parameters=[
                        dict(name="limit", **{"in": "query"}, schema=dict(
                            type="integer", minimum=1, maximum=3)),
                        dict(name="active", **{"in": "query"}, schema=dict(
                            type="boolean", enum=[True, False]))])}
            if index in {7, 31}:
                paths[f"/sections/{index}"]["get"]["parameters"].append(dict(
                    name="order", **{"in": "query"},
                    schema=dict(type="string", enum=["asc", "desc"])))
        paths["/private-profile"] = {"get": operation("readPrivateProfile", "读取受限配置",
            {"200": response(healthy)}, protected=True)}
        paths["/attachments"] = {"post": dict(operationId="uploadAttachment", summary="上传附件",
            requestBody=dict(required=True, content={"multipart/form-data": dict(schema=
                schema_object(dict(file=dict(type="string", format="binary"))))}),
            responses={"200": response(healthy)})}
        paths["/exports"] = {"post": operation("beginExport", "启动长时间导出任务",
            {"202": response(schema_object(dict(job=dict(type="string"))))})}
    else:
        raise ValueError("Unknown fixture project")
    result = dict(openapi="3.0.3", info=dict(title="Disposable " + kind, version="1"), paths=paths)
    if kind in {"reservations", "directory"}:
        result["components"] = dict(securitySchemes=dict(bearer=dict(type="http", scheme="bearer")))
    return result


def create_target(kind, environ=None, events=None, fault_http_status=503, read_delay=0,
                  delete_fault_status=None, delete_noop=False):
    if kind not in {"parcels", "reservations", "directory"}:
        raise ValueError("Unknown fixture project")
    if fault_http_status not in {200, 503}:
        raise ValueError("Unsupported disposable fault status")
    if not 0 <= read_delay <= 4:
        raise ValueError("Disposable read delay must be between zero and four seconds")
    if delete_fault_status is not None and delete_fault_status not in {500, 503}:
        # Only an isolated-lab switch for precondition evidence: a delete that never happens
        # must be reproducible against a real HTTP surface, not simulated in the product.
        raise ValueError("Unsupported disposable delete fault status")
    env = dict(os.environ if environ is None else environ)
    app = FastAPI(title="Disposable acceptance target")
    records = {}
    app.state.records = records
    app.state.requests = []

    def identity(request):
        authorization = request.headers.get("authorization", "")
        for alias in ("OWNER", "READER"):
            token = env.get(f"AGENTTEST_TARGET_LAB_{alias}_BEARER")
            if token and secrets.compare_digest(authorization, "Bearer " + token):
                return alias
        raise HTTPException(401, "Test identity required")

    def validate(body, schema):
        # Target-side validation is independent from AgentTest's request validator.
        if not Draft7Validator(schema).is_valid(body):
            raise HTTPException(422, "Input does not match document")

    @app.middleware("http")
    async def observe(request: Request, next_handler):
        # Disposable directory inputs only; never headers, credentials or request bodies.
        event = dict(method=request.method, path=request.url.path)
        if kind == "directory" and request.url.path.startswith("/sections/"):
            event["query"] = {key: request.query_params[key] for key in ("limit", "active", "order")
                              if key in request.query_params}
        app.state.requests.append(event)
        if events is not None:
            import json

            with events.open("a", encoding="utf-8") as output:
                output.write(json.dumps(event, ensure_ascii=False) + "\n")
        return await next_handler(request)

    @app.get("/health")
    def health():
        return dict(status="ok", disposable=True, instance_id=env.get("AGENTTEST_LAB_INSTANCE_ID"),
                    record_count=len(records))

    @app.get("/availability")
    def availability():
        return JSONResponse(dict(state="unavailable"), status_code=fault_http_status)

    if kind == "parcels":
        specification = document(kind)
        create_schema = specification["paths"]["/parcels"]["post"]["requestBody"][
            "content"]["application/json"]["schema"]

        @app.post("/parcels", status_code=201)
        async def create(request: Request):
            body = await request.json()
            validate(body, create_schema)
            key = "parcel_" + uuid4().hex[:12]
            records[key] = dict(id=key, **deepcopy(body))
            return records[key]

        @app.api_route("/parcels/{parcel_id}", methods=["GET", "PUT", "PATCH", "DELETE"])
        async def resource(parcel_id: str, request: Request):
            if request.method == "GET" and read_delay:
                import asyncio

                await asyncio.sleep(read_delay)
            if parcel_id not in records:
                raise HTTPException(404, "Missing disposable record")
            if request.method == "DELETE":
                if delete_fault_status is not None:
                    raise HTTPException(delete_fault_status, "Disposable delete fault")
                if delete_noop:
                    # A delete that answers 204 without deleting: the real defect a
                    # trustworthy "after deletion it must be 404" expectation has to catch.
                    return Response(status_code=204)
                del records[parcel_id]
                return Response(status_code=204)
            if request.method in {"PUT", "PATCH"}:
                body = await request.json()
                validate(body, create_schema if request.method == "PUT" else
                         schema_object(dict(name=create_schema["properties"]["name"])))
                records[parcel_id].update(body)
            return records[parcel_id]
    elif kind == "reservations":
        create_schema = document(kind)["paths"]["/reservations"]["post"]["requestBody"][
            "content"]["application/json"]["schema"]

        @app.post("/session")
        async def login(request: Request):
            body = await request.json()
            for alias in ("OWNER", "READER"):
                username = env.get(f"AGENTTEST_TARGET_LAB_{alias}_USERNAME")
                password = env.get(f"AGENTTEST_TARGET_LAB_{alias}_PASSWORD")
                if (username and password and secrets.compare_digest(str(body.get("username")),
                        username) and secrets.compare_digest(str(body.get("password")), password)):
                    return dict(access_token=env[f"AGENTTEST_TARGET_LAB_{alias}_BEARER"])
            raise HTTPException(401, "Unknown disposable identity")

        @app.post("/reservations", status_code=201)
        async def reserve(request: Request):
            owner = identity(request)
            body = await request.json()
            validate(body, create_schema)
            key = "reservation_" + uuid4().hex[:12]
            records[key] = dict(body=deepcopy(body), owner=owner)
            return dict(reservation_id=key, **body)

        @app.api_route("/reservations/{reservation_id}", methods=["GET", "DELETE"])
        async def reservation(reservation_id: str, request: Request):
            role = identity(request)
            record = records.get(reservation_id)
            if record is None:
                raise HTTPException(404, "Missing disposable record")
            if role != record["owner"]:
                raise HTTPException(403, "Not this disposable identity's resource")
            if request.method == "DELETE":
                del records[reservation_id]
                return Response(status_code=204)
            return dict(reservation_id=reservation_id, **record["body"])
    elif kind == "directory":
        @app.get("/sections/{section}")
        def section(section: int, request: Request):
            if not 0 <= section < 44:
                raise HTTPException(404)
            limit = request.query_params.get("limit", "2")
            active = request.query_params.get("active", "true")
            order = request.query_params.get("order", "asc")
            if (not limit.isdecimal() or not 1 <= int(limit) <= 3
                    or active not in {"true", "false"} or order not in {"asc", "desc"}):
                raise HTTPException(422, "Invalid disposable query")
            item = dict(key=section, label=f"section-{section}", active=active == "true")
            return dict(items=[item] * int(limit)) if section % 2 else dict(data=item)

        @app.get("/private-profile")
        def profile(request: Request):
            identity(request)
            return dict(state="ready")
    return app


def main():
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", choices=["parcels", "reservations", "directory"])
    parser.add_argument("--port", type=int)
    parser.add_argument("--documents", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--fault-http-status", type=int, choices=[200, 503], default=503)
    parser.add_argument("--read-delay", type=float, default=0)
    parser.add_argument("--delete-fault-status", type=int, choices=[500, 503], default=None)
    parser.add_argument("--delete-noop", action="store_true")
    args = parser.parse_args()
    if args.documents:
        args.documents.mkdir(parents=True, exist_ok=True)
        for kind in ("parcels", "reservations", "directory"):
            (args.documents / (kind + ".openapi.json")).write_text(json.dumps(
                document(kind), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.project:
        if args.port is None or not 1024 <= args.port <= 65535:
            parser.error("An explicit local port is required")
        uvicorn.run(create_target(args.project, events=args.events,
                                  fault_http_status=args.fault_http_status,
                                  read_delay=args.read_delay,
                                  delete_fault_status=args.delete_fault_status,
                                  delete_noop=args.delete_noop),
                    host="127.0.0.1", port=args.port,
                    proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
