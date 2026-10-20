"""Operator-owned mini-target facade. No MCP dependency and no second HTTP engine."""

import asyncio
import json
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import ValidationError

from agenttest.contracts import summarize_openapi
from agenttest.demo import PROJECT_ROOT, demo_inputs
from agenttest.diagnostics import Layer, ValidationFailure, schema_diagnostic
from agenttest.dsl import Scenario
from agenttest.executor import Executor
from agenttest.mini_target import create_target
from agenttest.planner_input import planning_context
from agenttest.policies import NeedsReview, PolicyRegistry
from agenttest.report import ReportStore, RunReport, fingerprint
from agenttest.tool_registry import REQUESTS, RunRequest

SCENARIOS = {"mini_normal": "none", "mini_ownership_fault": "ownership",
             "mini_terminal_fault": "terminal"}


@dataclass(frozen=True)
class MCPSettings:
    """Startup-only budgets; tool arguments cannot set these values."""

    max_runs: int = 3
    max_tool_calls: int = 100

    def __post_init__(self):
        if (type(self.max_runs) is not int or not 0 <= self.max_runs <= 20
                or type(self.max_tool_calls) is not int or not 1 <= self.max_tool_calls <= 1000):
            raise ValueError("MCP_SETTINGS_INVALID")


def error(code: str) -> dict:
    return {"ok": False, "error_code": code}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class MCPService:
    def __init__(self, root: Path, settings: MCPSettings | None = None):
        self._store = ReportStore(root)
        self._settings = settings or MCPSettings()
        self._runs = 0
        self._calls = 0
        self._lock = asyncio.Lock()
        self._scenario, self._config = demo_inputs()
        self._config.max_steps = 8
        self._config.max_requests = 10
        self._config.run_timeout = 30.0
        self._registry = PolicyRegistry.from_json(
            (PROJECT_ROOT / "examples/demo-policies.json").read_bytes())
        self._contract = summarize_openapi(create_target({}).openapi())

    def _executor(self, fault="none") -> Executor:
        # Synthetic credentials only: never load .env or inherit process secrets.
        environ = {"DEMO_ALICE_USERNAME": "alice", "DEMO_BOB_USERNAME": "bob",
                   "DEMO_ALICE_PASSWORD": secrets.token_urlsafe(24),
                   "DEMO_BOB_PASSWORD": secrets.token_urlsafe(24)}
        app = create_target({"alice": environ["DEMO_ALICE_PASSWORD"],
                             "bob": environ["DEMO_BOB_PASSWORD"]}, fault)
        return Executor(self._config, self._contract, self._store,
                        httpx.ASGITransport(app=app, raise_app_exceptions=False), environ,
                        registry=self._registry)

    async def call(self, name: str, arguments: dict | None = None) -> dict:
        """One bounded, redacted boundary shared by in-memory and stdio clients."""
        exhausted = self._charge()
        if exhausted:
            return exhausted
        if name not in REQUESTS:
            return error("MCP_TOOL_NOT_AUTHORIZED")
        try:
            if len(json.dumps(arguments).encode()) > 131_072:
                return error("MCP_INPUT_TOO_LARGE")
            request = REQUESTS[name].model_validate({} if arguments is None else arguments)
        except (ValidationError, ValueError, TypeError, RecursionError):
            return error("MCP_ARGUMENTS_INVALID")
        try:
            if name == "list_tests":
                return {"ok": True, "scenarios": list(SCENARIOS),
                        "operations": self.public_contract()["operations"],
                        "execution": "disposable_in_process_mini_target_only",
                        "courseinsight_enabled": False,
                        "budgets": {"runs_remaining": max(0, self._settings.max_runs - self._runs),
                                    "max_steps": 8, "max_requests_per_run": 10,
                                    "run_timeout_seconds": 30}}
            if name == "get_contract":
                return {"ok": True, **self.public_contract()}
            if name == "validate_plan":
                return self.validate(request.plan_json)
            if name == "run_scenario":
                async with self._lock:
                    return await self._run(request)
            return self._read(request.run_id)
        except Exception:
            # Do not pass exception text, Pydantic inputs or a traceback to the SDK logger.
            return error("MCP_INTERNAL_ERROR")

    def _charge(self) -> dict | None:
        self._calls += 1
        if self._calls > self._settings.max_tool_calls:
            return error("MCP_TOOL_BUDGET_EXHAUSTED")
        return None

    def reject(self, code: str) -> dict:
        """Malformed protocol calls consume the same startup tool budget."""
        return self._charge() or error(code)

    def public_contract(self) -> dict:
        context = planning_context(self._contract, self._config, self._registry,
                                   "查看已批准的独立演示测试", Scenario.model_json_schema())
        return {key: context[key] for key in ("operations", "security_schemes", "policies",
                                             "roles", "plan_schema")}

    def validate(self, raw: str) -> dict:
        try:
            payload = json.loads(raw, object_pairs_hook=unique_object)
        except (ValueError, RecursionError):
            return error("JSON_INVALID")
        try:
            scenario = Scenario.model_validate(payload)
            self._executor().validate(scenario)
        except ValidationError as exc:
            return {**error("DSL_INVALID"), "diagnostic":
                    schema_diagnostic(exc, Layer.DSL).model_dump(mode="json")}
        except NeedsReview as exc:
            return {**error("NEEDS_REVIEW"), "diagnostic": exc.diagnostic.model_dump(mode="json")}
        except ValidationFailure as exc:
            return {**error("PLAN_REJECTED"), "diagnostic": exc.diagnostic.model_dump(mode="json")}
        except (ValueError, RecursionError):
            return error("PLAN_REJECTED")
        return {"ok": True, "valid": True, "execution_authorized": False,
                "plan_sha256": fingerprint(scenario.model_dump())}

    def _directory(self, run_id: str) -> Path:
        if UUID(run_id).hex != run_id:
            raise ValueError("Invalid run id")
        directory = self._store.root / run_id
        # resolve expands Windows 8.3 aliases. Check links BEFORE comparing both
        # canonical paths; keep ReportStore's unresolved root for its pinned I/O.
        for path in (directory, *directory.parents):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Invalid run directory")
        if directory.resolve().parent != self._store.root.resolve():
            raise ValueError("Invalid run directory")
        return directory

    def _read(self, run_id: str) -> dict:
        directory = self._directory(run_id)
        claim = directory / "mcp-claim.json"
        if not claim.is_file() or claim.is_symlink():
            return error("MCP_RUN_NOT_FOUND")
        report_path = directory / "report.json"
        if report_path.is_symlink():
            return error("MCP_REPORT_UNAVAILABLE")
        try:
            report = self._store.read(run_id)
        except FileNotFoundError:
            return error("MCP_RUN_NOT_RESUMABLE")
        if not report.completed:
            return error("MCP_RUN_NOT_RESUMABLE")
        return {"ok": True, "report": report.model_dump(mode="json")}

    async def _run(self, request: RunRequest) -> dict:
        if request.scenario_id not in SCENARIOS:
            return error("MCP_SCENARIO_NOT_AUTHORIZED")
        directory = self._directory(request.invocation_id)
        claim = directory / "mcp-claim.json"
        if directory.exists():
            if claim.is_symlink() or not claim.is_file():
                return error("MCP_RUN_NOT_RESUMABLE")
            if json.loads(claim.read_bytes()) != {"scenario_id": request.scenario_id}:
                return error("MCP_INVOCATION_CONFLICT")
            result = self._read(request.invocation_id)
            return {**result, "replayed": False, "cached": result["ok"]}
        if self._runs >= self._settings.max_runs:
            return error("MCP_RUN_BUDGET_EXHAUSTED")
        self._runs += 1
        # Atomic directory claim is durable BEFORE Executor can send any request.
        # A crash, cancellation or failed report leaves it spent, including after restart.
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return error("MCP_RUN_NOT_RESUMABLE")
        claim.write_text(json.dumps({"scenario_id": request.scenario_id}), encoding="utf-8")
        executor = self._executor(SCENARIOS[request.scenario_id])
        report = RunReport(run_id=request.invocation_id,
                           scenario_sha256=fingerprint(self._scenario.model_dump()),
                           contract_sha256=fingerprint(self._contract.model_dump()),
                           environment=self._config.environment,
                           target_origin=self._config.base_url)
        report = await executor.run(self._scenario, report=report)
        return {"ok": True, "report": report.model_dump(mode="json"),
                "replayed": False, "cached": False}
