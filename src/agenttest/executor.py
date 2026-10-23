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
from agenttest.investigation import InvestigationConfig, ReadOnlyCapabilityRegistry
from agenttest.policies import NeedsReview, PolicyRegistry, RegisteredPolicy
from agenttest.report import (
    AttemptEvidence,
    CheckResult,
    InvestigationResult,
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
        *, investigation: InvestigationConfig | None = None,
        capabilities: ReadOnlyCapabilityRegistry | None = None, analyzer=None,
    ) -> None:
        self.config = config.model_copy(deep=True)
        self.contract = contract.model_copy(deep=True)
        self.store = store or ReportStore(Path("runs"))
        self.transport = transport
        self.environ = dict(os.environ) if environ is None else dict(environ)
        self.registry = (registry or PolicyRegistry()).snapshot()
        self.investigation = (investigation or InvestigationConfig()).model_copy(deep=True)
        self.capabilities = (capabilities or ReadOnlyCapabilityRegistry()).snapshot()
        self.analyzer = analyzer

    def validate(
        self, scenario: Scenario, *, report: RunReport | None = None,
    ) -> tuple[dict[str, Operation], dict[str, RegisteredPolicy]]:
        operations = validate_plan(scenario, self.contract, self.config, self.environ)
        if report is not None:
            report.dsl_valid = True
        policies, previous = {}, {}
        for step in scenario.steps:
            policies[step.id] = self.registry.resolve(
                step, self.config.business_scenario, previous)
            if policies[step.id].negative:
                from agenttest.negative import validate_approval

                if any(step.operation_id == r.login_operation for r in self.config.roles.values()):
                    raise NeedsReview("NEGATIVE_AUTHENTICATION_FORBIDDEN")
                validate_approval(policies[step.id].negative, operations[step.operation_id])
            previous[step.id] = step
        if report is not None:
            report.policy_authorized = True
        return operations, policies

    async def run(self, scenario: Scenario, *, report: RunReport | None = None) -> RunReport:
        scenario = scenario.model_copy(deep=True)
        report = report or RunReport(
            scenario_sha256=fingerprint(scenario.model_dump()),
            contract_sha256=fingerprint(self.contract.model_dump()),
            environment=self.config.environment, target_origin=self.config.base_url,
            task_mode="operator_plan",
        )
        if report.steps or report.completed:
            raise ValueError("RUN_NOT_RESUMABLE")
        try:
            saved = self.store.read(report.run_id)
        except FileNotFoundError:
            saved = None
        if saved is not None and (saved.steps or saved.completed):
            raise ValueError("RUN_NOT_RESUMABLE")
        report.scenario_sha256 = fingerprint(scenario.model_dump())
        report.contract_sha256 = fingerprint(self.contract.model_dump())
        report.target_config_sha256 = fingerprint(self.config.model_dump())
        report.policy_registry_sha256 = self.registry.digest()
        self.store.save(report)
        try:
            operations, policies = self.validate(scenario, report=report)
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
        authenticated: set[str] = set()
        budget = {"requests": 0, "last_request": 0.0}
        timeout = httpx.Timeout(
            self.config.read_timeout, connect=self.config.connect_timeout,
            write=self.config.read_timeout, pool=self.config.connect_timeout,
        )
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=timeout, trust_env=False,
                follow_redirects=False, limits=httpx.Limits(max_connections=1),
            ) as client:
                async with asyncio.timeout(self.config.run_timeout):
                    for step in scenario.steps:
                        operation = operations[step.operation_id]
                        result = StepResult(
                            step_id=step.id, operation_id=step.operation_id,
                            method=operation.method, path_template=operation.path,
                            outcome=Outcome.TEST_INFRA_ERROR, reason="IN_PROGRESS",
                            policy_sha256=fingerprint(policies[step.id].model_dump()),
                            policy_template_sha256=fingerprint(policies[step.id].model_dump()),
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
                                if step.role:
                                    # A role is reusable only after a successful original request.
                                    role = self.config.roles[step.role]
                                    if (step.operation_id == role.login_operation
                                            or operation.security):
                                        authenticated.add(step.role)
                            body = None
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
                if not report.reason:
                    report.outcome = aggregate(report.steps)
                # Publish the immutable business result before any optional investigation.
                report.completed = True
                self.store.save(report)
                if self.investigation.enabled:
                    from agenttest.readonly_runtime import investigate

                    await investigate(self, client, scenario, report, state, vault, authenticated,
                                      last_request=budget["last_request"])
        except asyncio.CancelledError:
            if report.investigations or report.feedback is not None:
                report.investigation_error = "RUN_CANCELLED"
            elif not report.completed:
                if report.steps:
                    result = report.steps[-1]
                    result.outcome = Outcome.TEST_INFRA_ERROR
                    result.reason = ("ATTEMPT_OUTCOME_UNKNOWN" if result.attempts
                                     and result.attempts[-1].error_code == "ATTEMPT_OUTCOME_UNKNOWN"
                                     else "RUN_CANCELLED")
                report.outcome, report.reason = Outcome.TEST_INFRA_ERROR, "RUN_CANCELLED"
            report.completed = True
            try:
                self.store.save(report)
            except OSError:
                # Best-effort journaling must not replace the caller's cancellation.
                pass
            raise
        except TimeoutError:
            if report.feedback is not None:
                report.investigation_error = "RUN_TIMEOUT"
            else:
                if report.steps:
                    report.steps[-1].outcome = Outcome.TEST_INFRA_ERROR
                    report.steps[-1].reason = "RUN_TIMEOUT"
                report.outcome, report.reason = Outcome.TEST_INFRA_ERROR, "RUN_TIMEOUT"
        except Exception:
            if report.investigations or report.feedback is not None:
                report.investigation_error = "INVESTIGATION_REPORT_ERROR"
            else:
                report.outcome, report.reason = Outcome.TOOL_ERROR, "TRANSPORT_SETUP_ERROR"
        finally:
            state.clear()
            authenticated.clear()
            vault.close()
        if not report.reason:
            report.outcome = aggregate(report.steps)
        report.completed = True
        try:
            self.store.save(report)
        except OSError:
            if not report.investigations and report.feedback is None:
                raise
            report.investigation_error = "INVESTIGATION_REPORT_ERROR"
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
        result.input_validation = "NORMAL_VALIDATED"
        if policy.negative:
            from agenttest.negative import mutate_request

            query, payload = mutate_request(policy.negative, operation,
                path_values, query, request_payload, media)
            if vault.contains_secret([query, payload]):
                raise ValidationFailure(Layer.DEPENDENCY, "DEPENDENCY_SECRET_VALUE")
            result.input_validation = "APPROVED_NEGATIVE"
            result.negative_test = policy.negative.kind
            result.approved_violation_codes = list(policy.negative.violation_codes)
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
            if policy.negative:
                result.negative_rejected_as_expected = result.outcome == Outcome.PASS
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
            if budget["requests"] >= budget.get("max_requests", self.config.max_requests):
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
            if isinstance(result, InvestigationResult):
                result.requests_used = len(result.attempts) + 1
                if report.feedback is not None:
                    report.feedback.requests_used = budget["requests"]
            budget["last_request"] = time.monotonic()
            evidence = AttemptEvidence(attempt=len(result.attempts) + 1, elapsed_ms=0.0)
            if isinstance(result, InvestigationResult):
                evidence.error_code = "ATTEMPT_OUTCOME_UNKNOWN"
            result.attempts.append(evidence)
            self.store.save(report)
            started = time.perf_counter()
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
                    evidence.error_code = None
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
            except BaseException:
                # Cancellation/process interruption leaves send outcome unknown, never retryable.
                evidence.error_code = "ATTEMPT_OUTCOME_UNKNOWN"
                raise
            finally:
                evidence.elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
                self.store.save(report)
        raise ExecutionLimit("RETRIES_EXHAUSTED")
