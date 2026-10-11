"""Local contract import and read-only run queries. Execution is operator-owned CLI."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from agenttest.contracts import MAX_DOCUMENT_BYTES, ContractSummary, load_openapi
from agenttest.report import ReportStore, RunReport


def create_app(runs_dir: Path = Path("runs")) -> FastAPI:
    app = FastAPI(title="AgentTest", version="0.1.0")
    store = ReportStore(runs_dir)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/spec/summary", response_model=ContractSummary)
    async def spec_summary(request: Request) -> ContractSummary:
        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type not in {"application/json", "application/yaml", "text/yaml", "text/plain"}:
            raise HTTPException(415, "Use JSON or YAML")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise HTTPException(413, "Contract exceeds size limit")
        try:
            return load_openapi(bytes(raw), unwrap=True)
        except (ValueError, TypeError, RecursionError):
            # Never echo the untrusted document (which may contain credentials).
            raise HTTPException(422, "Invalid or unsupported OpenAPI contract") from None

    @app.get("/api/runs/{run_id}", response_model=RunReport)
    def run_result(run_id: str) -> RunReport:
        try:
            return store.read(run_id)
        except (ValueError, OSError):
            raise HTTPException(404, "Run not found") from None

    return app


app = create_app()
