"""Single-use investigation inside Executor's live, non-resumable HTTP session."""

import asyncio
import time
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx

from agenttest.credentials import CredentialVault
from agenttest.dsl import ArgumentBinding, Scenario, Step
from agenttest.investigation import AnalyzerSelection
from agenttest.plan_json import bounded_json
from agenttest.report import InvestigationResult, Outcome, RunReport, fingerprint
from agenttest.selectors import select

if TYPE_CHECKING:
    from agenttest.executor import Executor


async def investigate(executor: "Executor", client: httpx.AsyncClient, scenario: Scenario,
                      report: RunReport, state: dict, vault: CredentialVault,
                      authenticated: set[str], *, last_request: float) -> None:
    """No public resume/batch/credential entry point; all handles expire on return."""
    if report.outcome != Outcome.ASSERTION_FAILED or not report.steps:
        return
    failed = report.steps[-1]
    if failed.outcome != Outcome.ASSERTION_FAILED or not failed.attempts:
        return
    original = report.model_copy(deep=True)
    config = executor.config.model_copy(deep=True)
    contract = executor.contract.model_copy(deep=True)
    registry = executor.registry.snapshot()
    capabilities = executor.capabilities.snapshot()
    authority = executor.investigation.model_copy(deep=True)
    environment = dict(executor.environ)

    def digest() -> str:
        return fingerprint([
            executor.config.model_dump(), executor.contract.model_dump(),
            executor.registry.digest(), [c.model_dump() for c in executor.capabilities.entries()],
            executor.investigation.model_dump(), executor.environ, scenario.model_dump(),
            report.model_dump(exclude={"investigations", "investigation_error"}),
        ])

    frozen = digest()
    previous = {s.id: s for s in scenario.steps[:len(report.steps)]}
    parent = previous[failed.step_id]
    handles: dict[str, tuple[str, str]] = {}
    choices, approved = [], {}
    # The only resource eligible is an already validated dependency of the failed step.
    for capability in capabilities.entries():
        for binding in parent.path_parameters.values():
            source = previous.get(binding.from_step)
            if (source is None or source.id not in state
                    or source.policy_id != capability.source_policy_id
                    or binding.select != capability.source_selector):
                continue
            try:
                value = select(state[source.id], binding.select)
                if type(value) not in {str, int} or vault.contains_secret(value):
                    continue
            except (ValueError, KeyError, TypeError, IndexError):
                continue
            key = (source.id, binding.select)
            handle = next((h for h, pair in handles.items() if pair == key), None) or uuid4().hex
            handles[handle] = key
            for role in capability.roles:
                if (role not in authenticated
                        or capability.business_scenario != config.business_scenario):
                    continue
                step = Step(
                    id="investigation_" + uuid4().hex, operation_id=capability.operation_id,
                    role=role, policy_id=capability.policy_id,
                    path_parameters={capability.path_parameter: ArgumentBinding(
                        from_step=source.id, select=capability.source_selector)},
                )
                try:
                    # Full DSL/OpenAPI/target/policy checks, without executing the prefix again.
                    ops, policies = executor.validate(Scenario(
                        name="read-only investigation", steps=[*previous.values(), step]))
                    operation, policy = ops[step.operation_id], policies[step.id]
                    rules = policy.binding_rules
                    if (operation.method != "GET" or policy.poll is not None
                            or not policy.closed_arguments or len(rules) != 1
                            or rules[0].location != "path_parameters"
                            or rules[0].name != capability.path_parameter
                            or rules[0].source_policy_id != capability.source_policy_id
                            or rules[0].source_selector != capability.source_selector
                            or any(step.operation_id == r.login_operation
                                   for r in config.roles.values())):
                        continue
                    choice = dict(capability_id=capability.id, role=role, resource_ref=handle)
                    choices.append(choice)
                    approved[(capability.id, role, handle)] = (step, operation, policy, source.id)
                except ValueError:
                    continue
    if not choices:
        return

    result = InvestigationResult(
        step_id="investigation_" + uuid4().hex, parent_step_id=failed.step_id,
        operation_id="", method="", path_template="", policy_sha256="",
        outcome=Outcome.NEEDS_REVIEW, reason="INVESTIGATION_PENDING",
    )
    report.investigations.append(result)
    started = time.perf_counter()
    budget = authority.budget
    requests = {"requests": 0, "last_request": last_request,
                "max_requests": min(budget.max_requests, config.max_requests)}
    try:
        if not all((budget.max_requests, budget.max_rounds,
                    budget.max_model_calls, budget.timeout)):
            result.outcome = Outcome.TEST_INFRA_ERROR
            result.reason = "INVESTIGATION_BUDGET_EXHAUSTED"
            return
        if executor.analyzer is None:
            result.reason = "INVESTIGATION_ANALYZER_UNAVAILABLE"
            return
        # Catalog contains no resource values, raw responses, credentials or internal target.
        context = {"failure": {"outcome": failed.outcome.value,
                                "status_code": failed.attempts[-1].status_code,
                                "failed_checks": [c.kind for c in failed.checks if not c.passed]},
                   "choices": choices}
        if vault.contains_secret(context):
            result.reason = "INVESTIGATION_CONTEXT_REJECTED"
            return
        async with asyncio.timeout(budget.timeout):
            result.rounds_used = result.model_calls_used = 1
            executor.store.save(report)
            raw = await executor.analyzer.generate(context)
            if digest() != frozen:
                result.reason = "INVESTIGATION_AUTHORITY_CHANGED"
                return
            if vault.contains_secret(raw):
                result.reason = "INVESTIGATION_SELECTION_REJECTED"
                return
            try:
                selection = AnalyzerSelection.model_validate(bounded_json(raw, 4096))
                entry = approved.get((selection.capability_id, selection.role,
                                      selection.resource_ref))
                if entry is None:
                    result.reason = "INVESTIGATION_SELECTION_REJECTED"
                    return
            except (ValueError, TypeError, RecursionError):
                result.reason = "INVESTIGATION_SELECTION_REJECTED"
                return
            finally:
                raw = None
            step, operation, policy, source_id = entry
            result.step_id, result.operation_id = step.id, step.operation_id
            result.method, result.path_template = operation.method, operation.path
            result.capability_id, result.source_step_id = selection.capability_id, source_id
            result.role = selection.role
            result.resource_ref_sha256 = fingerprint(selection.resource_ref)
            result.policy_sha256 = fingerprint(policy.model_dump())
            # Authentication was established in the original run, never by investigation.
            vault.header(step.role)
            await executor._step(client, step, operation, state, requests, result,
                                 report, policy, vault)
            if digest() != frozen:
                result.outcome = Outcome.NEEDS_REVIEW
                result.reason = "INVESTIGATION_AUTHORITY_CHANGED"
            else:
                result.reason = ("INVESTIGATION_POLICY_PASSED" if result.outcome == Outcome.PASS
                                 else "INVESTIGATION_ASSERTION_FAILED")
    except TimeoutError:
        result.outcome, result.reason = Outcome.TEST_INFRA_ERROR, "INVESTIGATION_TIMEOUT"
    except httpx.TransportError:
        result.outcome, result.reason = Outcome.TEST_INFRA_ERROR, "INVESTIGATION_TRANSPORT_ERROR"
    except OSError:
        result.outcome, result.reason = Outcome.TOOL_ERROR, "INVESTIGATION_REPORT_ERROR"
        report.investigation_error = result.reason
    except asyncio.CancelledError:
        result.outcome = Outcome.TEST_INFRA_ERROR
        result.reason = ("ATTEMPT_OUTCOME_UNKNOWN" if result.attempts
                         else "INVESTIGATION_CANCELLED")
        raise
    except Exception as exc:
        from agenttest.executor import ExecutionLimit

        if isinstance(exc, ExecutionLimit):
            result.outcome = Outcome.TEST_INFRA_ERROR
            result.reason = "INVESTIGATION_SAFETY_LIMIT"
        else:
            result.outcome, result.reason = Outcome.TOOL_ERROR, "INVESTIGATION_TOOL_ERROR"
    finally:
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        result.requests_used = requests["requests"]
        # Restore the immutable original evidence even if an injected analyzer mutated a reference.
        for name in type(report).model_fields:
            if name not in {"investigations", "investigation_error"}:
                setattr(report, name, getattr(original, name))
        executor.config, executor.contract, executor.registry = config, contract, registry
        executor.capabilities = capabilities
        executor.investigation, executor.environ = authority, environment
        handles.clear()
        approved.clear()
        try:
            executor.store.save(report)
        except OSError:
            report.investigation_error = "INVESTIGATION_REPORT_ERROR"
