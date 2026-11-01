"""One purposeful read-only recheck with the original role, inputs, policy and resource."""

import asyncio

import httpx

from agenttest.report import InvestigationResult, Outcome, fingerprint


async def recheck_read(executor, client, scenario, report, state, budget, policies, vault):
    """Preserve the original failure; never replay a write or infer an unobserved root cause."""
    if report.outcome not in {Outcome.ASSERTION_FAILED, Outcome.TEST_INFRA_ERROR}:
        return
    if not report.steps:
        return
    failed = report.steps[-1]
    step = next(s for s in scenario.steps if s.id == failed.step_id)
    operation = next(o for o in executor.contract.operations if o.operation_id == step.operation_id)
    if operation.method not in {"GET", "HEAD", "OPTIONS"} or step.poll or step.read_retries:
        report.investigation_error = "NO_AUTHORIZED_READONLY_PROBE"
        return
    policy = policies[step.id]
    probe = InvestigationResult(step_id=step.id + "_read_probe", parent_step_id=step.id,
        operation_id=step.operation_id, method=operation.method, path_template=operation.path,
        outcome=Outcome.TEST_INFRA_ERROR, reason="PRODUCT_READ_PROBE_PENDING",
        policy_sha256=fingerprint(policy.model_dump()),
        policy_template_sha256=fingerprint(policy.model_dump()), role=step.role)
    report.investigations.append(probe)
    executor.store.save(report)
    before = budget["requests"]
    ledger = getattr(executor.transport, "ledger", None)
    dispatched_before = ledger.requests if ledger is not None else before
    try:
        async with asyncio.timeout(min(15.0, executor.config.connect_timeout
                                       + executor.config.read_timeout + 1)):
            await executor._step(client, step, operation, state, budget, probe, report,
                                 policy, vault)
        probe.reason = "PRODUCT_READ_PROBE_EVALUATED"
    except (httpx.TransportError, TimeoutError):
        probe.reason = "PRODUCT_READ_PROBE_ENVIRONMENT_ERROR"
    except Exception:
        # Exception strings may contain transport input; keep only an allowlisted diagnosis.
        probe.reason = "PRODUCT_READ_PROBE_STOPPED"
    finally:
        probe.requests_used = ((ledger.requests if ledger is not None else budget["requests"])
                               - dispatched_before)
        probe.rounds_used = 1
        executor.store.save(report)
