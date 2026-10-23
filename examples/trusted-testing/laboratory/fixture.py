"""Disposable two-producer storage graph; no external service or persistent business data."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

ENVIRON = {"LAB_TECH_BEARER": "synthetic-laboratory-tech-access",
           "LAB_AUDITOR_BEARER": "synthetic-laboratory-auditor-access"}


def runtime(fault="none"):
    if fault not in {"none", "readback", "owner_leak", "accept_invalid", "evidence_gap"}:
        raise ValueError("UNKNOWN_SYNTHETIC_FAULT")
    app = FastAPI()
    samples, trays, placements = {}, {}, {}
    reads = {}
    app.state.requests = 0

    def role(request):
        header = request.headers.get("authorization")
        aliases = (("tech", "LAB_TECH_BEARER"), ("auditor", "LAB_AUDITOR_BEARER"))
        return next((r for r, key in aliases if header == "Bearer " + ENVIRON[key]), None)

    def error(code="INVALID_INPUT", status=422):
        return JSONResponse({"code": code}, status_code=status)

    @app.middleware("http")
    async def observed(request, call_next):
        app.state.requests += 1
        return await call_next(request)

    @app.post("/lab/specimens")
    async def sample(request: Request):
        if role(request) != "tech":
            return error("ACCESS_DENIED", 403)
        body = await request.json()
        valid = (type(body) is dict and set(body) == {"label", "aliquots"}
            and type(body["label"]) is str and 3 <= len(body["label"]) <= 8
            and type(body["aliquots"]) is int and 1 <= body["aliquots"] <= 4)
        if not valid and fault != "accept_invalid":
            return error()
        key = "spec_" + str(len(samples) + 1)
        samples[key] = dict(key=key, label=body.get("label"), aliquots=body.get("aliquots"))
        return JSONResponse({"sample": samples[key]}, status_code=201)

    @app.post("/lab/trays")
    async def tray(request: Request):
        if role(request) != "tech":
            return error("ACCESS_DENIED", 403)
        body = await request.json()
        valid = type(body) is dict and set(body) == {"zone"} and body["zone"] in {"cold", "ambient"}
        if not valid and fault != "accept_invalid":
            return error()
        key = "tray_" + str(len(trays) + 1)
        trays[key] = dict(key=key, zone=body.get("zone"))
        return JSONResponse({"tray": trays[key]}, status_code=201)

    @app.post("/lab/trays/{tray_key}/specimens/{specimen_key}")
    async def place(tray_key: str, specimen_key: str, request: Request):
        if role(request) != "tech":
            return error("ACCESS_DENIED", 403)
        if tray_key not in trays or specimen_key not in samples:
            return error("RESOURCE_NOT_FOUND", 404)
        body = await request.json()
        valid = (type(body) is dict and set(body) == {"position"}
                 and type(body["position"]) is int and 1 <= body["position"] <= 8)
        if not valid and fault != "accept_invalid":
            return error()
        key = "placement_" + str(len(placements) + 1)
        placements[key] = dict(key=key, specimen_key=specimen_key, tray_key=tray_key,
                               position=body.get("position"), status="stored")
        return JSONResponse({"placement": placements[key]}, status_code=201)

    @app.get("/lab/placements/{placement_key}")
    async def lookup(placement_key: str, request: Request):
        current_role = role(request)
        if current_role != "tech" and not (current_role == "auditor" and fault == "owner_leak"):
            return error("ACCESS_DENIED", 403)
        if placement_key not in placements:
            return error("RESOURCE_NOT_FOUND", 404)
        reads[placement_key] = reads.get(placement_key, 0) + 1
        result = dict(placements[placement_key])
        if fault == "readback" or fault == "evidence_gap" and reads[placement_key] == 1:
            result["status"] = "lost"
        return {"placement": result}

    return app, dict(ENVIRON)
