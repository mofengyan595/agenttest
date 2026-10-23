"""Disposable input-validation target, independent from the executor's schema validator."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_negative_target(fault="none"):
    if fault not in {"none", "accept_invalid", "wrong_error"}:
        raise ValueError("UNKNOWN_SYNTHETIC_FAULT")
    app = FastAPI()

    @app.post("/validation-records")
    async def create(request: Request):
        body = await request.json()
        valid = (isinstance(body, dict) and set(body) == {"message", "category", "count"}
            and type(body["message"]) is str and 2 <= len(body["message"]) <= 8
            and body["category"] in ["a", "b"] and type(body["count"]) is int
            and 1 <= body["count"] <= 5)
        if not valid and fault != "accept_invalid":
            return JSONResponse({"code": "WRONG_ERROR" if fault == "wrong_error"
                                 else "INVALID_INPUT"}, status_code=422)
        return JSONResponse({"created": True}, status_code=201)

    return app
