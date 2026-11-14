"""The adaptive execution budget: estimation, growth, hard stops and FIXED compatibility."""

import pytest
from test_product_design import project as design_project

from agenttest.execution_budget import (
    ABSOLUTE_MAX_MODEL_CALLS,
    ADAPTIVE,
    FIXED,
    ExecutionBudget,
    ExecutionState,
    estimate_round,
    resolve_fallback,
    resolve_plan,
)
from agenttest.project_goals import ProjectBudget


class Target:
    max_requests = 30


def fallback(**overrides):
    base = dict(model_calls=8, total_tokens=100_000, agent_turns=16, agent_tool_calls=24,
                http_requests=30, seconds=420.0)
    base.update(overrides)
    return base


def plan_for(*, mode=ADAPTIVE, target=None, estimate=None, **overrides):
    budget = ExecutionBudget(mode=mode, **overrides)
    return budget, resolve_plan(budget=budget, target=target or Target(),
                                fallback=fallback(), estimate=estimate)


def summary_with(plans=()):
    cases = [dict(status="FINISHED", case_id=name, resource_lifecycle=[],
                  evidence=[dict(executed=True, policy_id=policy) for policy in policies])
             for name, policies in plans]
    return dict(cases=cases)


def test_fixed_mode_uses_the_configured_numbers_and_never_grows():
    budget, plan = plan_for(mode=FIXED)
    assert plan.mode == FIXED
    assert plan.limit("model_calls") == 8
    assert plan.limit("http_requests") == 30
    assert plan.limit("seconds") == 420.0
    state = ExecutionState(plan, budget=budget)
    assert state.observe({"cases": []})["growths"] == []
    assert state.observe(summary_with([("c1", ["p1"])]))["growths"] == []
    assert state.stopped is False
    assert state.public()["mode"] == FIXED


def test_adaptive_allowance_never_widens_the_business_http_permission():
    project, _ = design_project()
    estimate = estimate_round(project=project, summary={"groups": []},
                              group_operations=["insertJob"])
    assert estimate.requests_min >= 1 and estimate.requests_max >= estimate.requests_min

    class Narrow(Target):
        max_requests = 4

    # A narrow operator approval makes the estimate an authority question, not a budget.
    budget = ExecutionBudget(mode=ADAPTIVE)
    plan = resolve_plan(budget=budget, target=Narrow(),
                        fallback=fallback(http_requests=4), estimate=estimate)
    assert plan.limit("http_requests") == 4
    assert plan.grants["http_requests"].source.startswith("target.max_requests")


def test_an_estimate_beyond_the_approval_is_reported_and_not_spent():
    project, _ = design_project()

    class Tiny(Target):
        max_requests = 1

    budget = ExecutionBudget(mode=ADAPTIVE)
    estimate = estimate_round(project=project, summary={"groups": []},
                              group_operations=["insertJob", "readJob"])
    plan = resolve_plan(budget=budget, target=Tiny(),
                        fallback=fallback(http_requests=1), estimate=estimate)
    if estimate.requests_max > 1:
        assert plan.authority["code"] == "HTTP_AUTHORITY_INSUFFICIENT"
        assert plan.authority["approved_requests"] == 1
        assert plan.limit("http_requests") == 1


def test_growth_needs_progress_and_stops_at_the_ceiling():
    budget, plan = plan_for()
    state = ExecutionState(plan, budget=budget)
    first = state.observe(summary_with([("c1", ["p1"])]))
    assert first["growths"] == []          # the first evidence is the initial observation
    second = state.observe(summary_with([("c1", ["p1"]), ("c2", ["p2"])]))
    assert "model_calls" in second["growths"]
    assert state.limit("model_calls") == 8 + budget.growth_model_calls
    for index in range(40):
        state.observe(summary_with([(f"c{index + 3}", [f"p{index + 3}"])]))
    assert state.limit("model_calls") == budget.model_calls_ceiling
    assert state.limit("http_requests") == 30  # never grown by progress


def test_two_stagnant_observations_stop_the_round_without_a_model_call():
    budget, plan = plan_for()
    state = ExecutionState(plan, budget=budget)
    state.observe(summary_with([("c1", ["p1"])]))
    same = summary_with([("c1", ["p1"])])
    assert state.observe(same)["progress"] is False
    assert state.stopped is False
    result = state.observe(same)
    assert result["no_progress_rounds"] == budget.no_progress_limit
    assert state.stopped is True
    assert state.stop_reason == "NO_PROGRESS"


def test_the_session_observation_uses_the_flat_delta_contract():
    """The runtime reads ``progress``/``kinds`` directly; a nested ``delta`` used to crash."""
    from types import SimpleNamespace

    from agenttest.product_agent import AgentSession

    budget, plan = plan_for()
    state = ExecutionState(plan, budget=budget)

    class Stub:
        execution = state
        summary = summary_with([("c1", [])])
        budget_trace = []
        ledger = SimpleNamespace(model_calls=0, requests=0)
        stop = "RUNNING"
        started = 0.0

        def _sync_allowance(self):
            return None

        def _stop(self, reason):
            self.stop = reason

    stub = Stub()
    first = AgentSession.observe_progress(stub, trigger="first")
    assert first["progress"] is True, "the first observation always establishes a baseline"
    second = AgentSession.observe_progress(stub, trigger="second")
    assert second["progress"] is False
    assert second["kinds"] == []
    assert stub.budget_trace, "a stagnant observation is recorded in the budget trace"
    assert stub.budget_trace[-1]["kinds"] == []
    assert stub.budget_trace[-1]["trigger"] == "second"


def test_cleanup_completion_counts_as_progress():
    budget, plan = plan_for()
    state = ExecutionState(plan, budget=budget)
    state.observe(summary_with([("c1", [])]))
    pending = summary_with([("c1", [])])
    pending["cases"][0]["resource_lifecycle"] = [
        dict(create_step="s1", status="OWNED", cleanup_result="NOT_SENT")]
    state.observe(pending)
    cleaned = summary_with([("c1", [])])
    cleaned["cases"][0]["resource_lifecycle"] = [
        dict(create_step="s1", status="CLEANED", cleanup_result="PASS")]
    delta = state.observe(cleaned)
    assert "CLEANUP_COMPLETED" in delta["kinds"]


def test_time_growth_keeps_the_cleanup_reserve():
    budget, plan = plan_for(seconds=120.0, seconds_ceiling=240.0)
    state = ExecutionState(plan, budget=budget)
    state.observe(summary_with([("c1", ["p1"])]))
    for index in range(20):
        state.observe(summary_with([(f"c{index + 2}", [f"p{index + 2}"])]))
    assert state.limit("seconds") == pytest.approx(
        240.0 - budget.cleanup_reserve_seconds)


def test_a_fixed_project_budget_keeps_its_historical_bytes():
    legacy = ProjectBudget()
    payload = legacy.model_dump(mode="json")
    assert "execution" not in payload
    assert ProjectBudget.model_validate(payload).execution.mode == ADAPTIVE
    explicit = ProjectBudget(execution=ExecutionBudget(mode=FIXED, model_calls=16))
    payload = explicit.model_dump(mode="json")
    assert payload["execution"]["mode"] == FIXED
    assert ProjectBudget.model_validate(payload).execution.model_calls == 16


def test_execution_limits_are_validated_and_bounded():
    with pytest.raises(ValueError):
        ExecutionBudget(model_calls=ABSOLUTE_MAX_MODEL_CALLS + 1)
    with pytest.raises(ValueError):
        ExecutionBudget(model_calls=30, model_calls_ceiling=10)
    with pytest.raises(ValueError):
        ExecutionBudget(no_progress_limit=0)
    with pytest.raises(ValueError):
        ExecutionBudget(mode="SOMETHING")


def test_estimate_reports_a_range_with_basis_and_unknowns():
    project, _ = design_project()
    estimate = estimate_round(project=project, summary={"groups": []},
                              group_operations=["insertJob", "readJob"])
    view = estimate.public()
    assert view["schema"] == "agenttest.execution-estimate.v1"
    assert view["model_calls"] == dict(min=estimate.model_calls_min,
                                       max=estimate.model_calls_max)
    assert view["requests"] == dict(min=estimate.requests_min, max=estimate.requests_max,
                                    basis=view["requests"]["basis"])
    assert view["rows"], "the estimate must publish why it needs those numbers"
    assert isinstance(view["unknowns"], list)


def test_fallback_is_the_existing_product_numbers():
    project, _ = design_project()
    numbers = resolve_fallback(project, {"budget": {"max_seconds": 420.0}})
    assert numbers["model_calls"] >= 1
    assert numbers["seconds"] == pytest.approx(420.0)
    assert numbers["http_requests"] == project.target.max_requests
