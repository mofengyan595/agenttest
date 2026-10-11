"""Deterministic HTTP execution of prevalidated plans; no generated code or shell."""

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from agenttest.contracts import ContractSummary, Operation
from agenttest.credentials import CredentialVault
from agenttest.diagnostics import Layer, ValidationFailure
from agenttest.dsl import (
    ArgumentBinding,
    AssertionPolicy,
    FieldAssertion,
    Scenario,
    Step,
    validate_plan,
)
from agenttest.policies import NeedsReview, PolicyRegistry, RegisteredPolicy
from agenttest.report import (
    AttemptEvidence,
    CheckResult,
    Outcome,
    ReportStore,
    RunReport,
    StepResult,
    aggregate,
    fingerprint,
)
from agenttest.request_validation import validate_request
from agenttest.safety import TargetConfig
from agenttest.selectors import select
from agenttest.verifier import verify


class ExecutionLimit(Exception):
    """A safety limit was reached, never evidence of a business defect."""


class Executor:
    def __init__(
        self, config: TargetConfig, contract: ContractSummary,
        store: ReportStore | None = None, transport: httpx.AsyncBaseTransport | None = None,
        environ: dict[str, str] | None = None,
        registry: PolicyRegistry | None = None,
    ) -> None:
        self.config = config
        self.contract = contract
        self.store = store or ReportStore(Path("runs"))
        self.transport = transport
        self.environ = dict(os.environ) if environ is None else dict(environ)
        self.registry = registry or PolicyRegistry()

    def validate(
        self, scenario: Scenario,
    ) -> tuple[dict[str, Operation], dict[str, RegisteredPolicy]]:
        operations = validate_plan(scenario, self.contract, self.config, self.environ)
        policies, previous = {}, {}
        for step in scenario.steps:
            policies[step.id] = self.registry.resolve(
                step, self.config.business_scenario, previous)
            previous[step.id] = step
        return operations, policies

    async def run(self, scenario: Scenario, *, report: RunReport | None = None) -> RunReport:
        report = report or RunReport(
            scenario_sha256=fingerprint(scenario.model_dump()),
            contract_sha256=fingerprint(self.contract.model_dump()),
            environment=self.config.environment, target_origin=self.config.base_url,
        )
        report.scenario_sha256 = fingerprint(scenario.model_dump())
        report.contract_sha256 = fingerprint(self.contract.model_dump())
        self.store.save(report)
        try:
            operations, policies = self.validate(scenario)
        except NeedsReview as exc:
            report.diagnostic = exc.diagnostic
            report.outcome, report.reason = Outcome.NEEDS_REVIEW, "REVIEW_REQUIRED"
            report.completed = True
            self.store.save(report)
            return report
        except ValueError as exc:
            if isinstance(exc, ValidationFailure):
                report.diagnostic = exc.diagnostic
            report.outcome, report.reason = Outcome.TOOL_ERROR, "PLAN_REJECTED"
            report.completed = True
            self.store.save(report)
            return report
        # Per-run state and budgets are never shared across executions.
        state: dict[str, Any] = {}
        vault = CredentialVault(self.config, self.environ)
        budget = {"requests": 0, "last_request": 0.0}
        timeout = httpx.Timeout(
            self.config.read_timeout, connect=self.config.connect_timeout,
            write=self.config.read_timeout, pool=self.config.connect_timeout,
        )
        try:
            async with asyncio.timeout(self.config.run_timeout):
                async with httpx.AsyncClient(
                    transport=self.transport, timeout=timeout, trust_env=False,
                    follow_redirects=False, limits=httpx.Limits(max_connections=1),
                ) as client:
                    for step in scenario.steps:
                        operation = operations[step.operation_id]
                        result = StepResult(
                            step_id=step.id, operation_id=step.operation_id,
                            method=operation.method, path_template=operation.path,
                            outcome=Outcome.TEST_INFRA_ERROR, reason="IN_PROGRESS",
                            policy_sha256=fingerprint(policies[step.id].model_dump()),
                        )
                        report.steps.append(result)
                        # Journal before sending a mutation. Reports cannot be resumed/replayed.
                        self.store.save(report)
                        try:
                            body = await self._step(
                                client, step, operation, state, budget, result, report,
                                policies[step.id], vault,
                            )
                            if result.outcome == Outcome.PASS:
                                state[step.id] = vault.public_response(body)
                        except httpx.TransportError as exc:
                            result.outcome = Outcome.TEST_INFRA_ERROR
                            result.reason = type(exc).__name__
                        except NeedsReview as exc:
                            report.diagnostic = exc.diagnostic
                            result.outcome = Outcome.NEEDS_REVIEW
                            result.reason = "REQUEST_SCHEMA_REVIEW_REQUIRED"
                        except ValidationFailure as exc:
                            report.diagnostic = exc.diagnostic
                            result.outcome, result.reason = Outcome.TOOL_ERROR, "EXECUTION_ERROR"
                        except ExecutionLimit as exc:
                            result.outcome = Outcome.TEST_INFRA_ERROR
                            result.reason = str(exc)
                        except Exception:
                            # Do not log exception text: transports may include tokens or bodies.
                            result.outcome, result.reason = Outcome.TOOL_ERROR, "EXECUTION_ERROR"
                        self.store.save(report)
                        if result.outcome != Outcome.PASS:
                            break
        except TimeoutError:
            if report.steps:
                report.steps[-1].outcome = Outcome.TEST_INFRA_ERROR
                report.steps[-1].reason = "RUN_TIMEOUT"
            report.outcome, report.reason = Outcome.TEST_INFRA_ERROR, "RUN_TIMEOUT"
        except Exception:
            report.outcome, report.reason = Outcome.TOOL_ERROR, "TRANSPORT_SETUP_ERROR"
        if not report.reason:
            report.outcome = aggregate(report.steps)
        report.completed = True
        self.store.save(report)
        return report

    def _binding(self, binding: ArgumentBinding, step: Step, state: dict) -> Any:
        if binding.from_step:
            try:
                return select(state[binding.from_step], binding.select)
            except (KeyError, ValueError, TypeError, IndexError):
                raise ValidationFailure(Layer.DEPENDENCY,
                                        "DEPENDENCY_RESOLUTION_FAILED") from None
        return binding.value

    async def _step(
        self, client: httpx.AsyncClient, step: Step, operation: Operation,
        state: dict, budget: dict, result: StepResult, report: RunReport,
        policy: RegisteredPolicy, vault: CredentialVault,
    ) -> Any:
        path = operation.path
        path_values = {}
        for name, binding in step.path_parameters.items():
            value = self._binding(binding, step, state)
            path_values[name] = value
            if type(value) not in {str, int} or not re.fullmatch(r"[A-Za-z0-9_-]+", str(value)):
                raise ValidationFailure(Layer.DEPENDENCY, "DEPENDENCY_UNSAFE_PATH_VALUE")
            path = path.replace("{" + name + "}", str(value))
        headers = {}
        role = self.config.roles.get(step.role)
        is_login = role and step.operation_id == role.login_operation
        if role and operation.security and {} not in operation.security:
            headers = vault.header(step.role)
        query = {key: self._binding(value, step, state) for key, value in step.query.items()}
        payload = {key: self._binding(value, step, state) for key, value in step.body.items()}
        ordinary_data = [path, query, payload, step.upload.model_dump() if step.upload else ""]
        if vault.contains_secret(ordinary_data):
            raise ValidationFailure(Layer.DEPENDENCY, "DEPENDENCY_SECRET_VALUE")
        if is_login:
            payload = vault.login_payload(step.role)
        media = ("multipart/form-data" if step.upload else "application/json"
                 if step.body or is_login else None)
        request_payload = {step.upload.field: step.upload.content} if step.upload else payload
        validate_request(operation, path_values, query, request_payload, media)
        policy = policy.model_copy(deep=True)
        policy.assertions = self.registry.request_assertions(policy, {
            "path_parameters": path_values, "query": query, "body": request_payload,
        })
        result.policy_sha256 = fingerprint(policy.model_dump())
        kwargs: dict[str, Any] = {"headers": headers, "params": query}
        if step.upload:
            kwargs["files"] = {step.upload.field: (
                step.upload.filename, step.upload.content.encode("utf-8"), "text/csv",
            )}
        elif step.body or is_login:
            kwargs["json"] = payload
        poll = policy.poll if step.poll else None
        attempts = poll.max_attempts if poll else 1
        for poll_attempt in range(attempts):
            status, body = await self._request(
                client, operation.method, self.config.base_url + path, kwargs,
                step.read_retries, budget, result, report,
            )
            if 300 <= status < 400:
                raise ExecutionLimit("REDIRECT_BLOCKED")
            if poll:
                progress = AssertionPolicy(
                    reviewed=True, source=policy.assertions.source,
                    status=policy.assertions.status,
                    fields=[FieldAssertion(select=poll.select, operator="in",
                                           expected=poll.progress_values + poll.terminal_values),
                            *poll.progress_fields],
                    public_evidence=policy.assertions.public_evidence,
                )
                result.outcome, result.checks = self._verify(progress, status, body, vault)
                if result.outcome != Outcome.PASS:
                    result.reason = "POLL_CONTRACT_VIOLATION"
                    return body
                terminal = select(body, poll.select) in poll.terminal_values
                if not terminal:
                    if poll_attempt + 1 == attempts:
                        raise ExecutionLimit("POLL_EXHAUSTED")
                    await asyncio.sleep(poll.interval)
                    continue
            result.outcome, result.checks = self._verify(policy.assertions, status, body, vault)
            result.reason = "POLICY_EVALUATED"
            if is_login and result.outcome == Outcome.PASS:
                vault.capture(step.role, body)
                return {}
            return body
        raise ExecutionLimit("POLL_EXHAUSTED")

    def _verify(
        self, policy: AssertionPolicy, status: int, body: Any, vault: CredentialVault,
    ) -> tuple[Outcome, list[CheckResult]]:
        outcome, checks = verify(policy, status, body, self.config.sensitive_fields)
        for check in checks:
            for field in ("expected", "actual"):
                if vault.contains_secret(getattr(check, field)):
                    setattr(check, field, "[REDACTED]")
        return outcome, checks

    async def _request(
        self, client: httpx.AsyncClient, method: str, url: str, kwargs: dict,
        retries: int, budget: dict, result: StepResult, report: RunReport,
    ) -> tuple[int, Any]:
        for retry in range(retries + 1):
            if budget["requests"] >= self.config.max_requests:
                raise ExecutionLimit("REQUEST_LIMIT")
            delay = self.config.min_request_interval - (time.monotonic() - budget["last_request"])
            if delay > 0:
                await asyncio.sleep(delay)
            # Prevent cookies acquired under one role leaking into a later role.
            client.cookies.clear()
            request = client.build_request(method, url, **kwargs)
            request_body = await request.aread()
            if len(request_body) > self.config.max_body_bytes:
                raise ExecutionLimit("REQUEST_BODY_LIMIT")
            budget["requests"] += 1
            budget["last_request"] = time.monotonic()
            evidence = AttemptEvidence(attempt=len(result.attempts) + 1, elapsed_ms=0.0)
            result.attempts.append(evidence)
            self.store.save(report)
            started = time.monotonic()
            try:
                response = await client.send(request, stream=True)
                try:
                    evidence.status_code = response.status_code
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        chunks.extend(chunk)
                        if len(chunks) > self.config.max_body_bytes:
                            raise ExecutionLimit("RESPONSE_BODY_LIMIT")
                    evidence.response_bytes = len(chunks)
                    evidence.response_sha256 = hashlib.sha256(chunks).hexdigest()
                    try:
                        body = json.loads(chunks)
                    except (ValueError, UnicodeError):
                        body = None
                    return response.status_code, body
                finally:
                    await response.aclose()
            except httpx.TransportError as exc:
                evidence.error_code = type(exc).__name__
                if retry == retries:
                    raise
            except ExecutionLimit as exc:
                evidence.error_code = str(exc)
                raise
            finally:
                evidence.elapsed_ms = round((time.monotonic() - started) * 1000, 3)
                self.store.save(report)
        raise ExecutionLimit("RETRIES_EXHAUSTED")
