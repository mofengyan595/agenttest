"""Minimal runnable application. No LLM, shell execution or external HTTP calls."""

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agenttest.contracts import ContractSummary, summarize_openapi

app = FastAPI(title="AgentTest", version="0.1.0")


class SpecRequest(BaseModel):
    document: dict[str, Any]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/spec/summary", response_model=ContractSummary)
def spec_summary(payload: SpecRequest) -> ContractSummary:
    try:
        return summarize_openapi(payload.document)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
