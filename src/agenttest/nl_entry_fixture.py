"""Offline fixture planner only. Never a live-model fallback or a product executor."""

import json
import time

import httpx

from agenttest.dsl import ArgumentBinding, Scenario, Step
from agenttest.policies import dependency_roles_match
from agenttest.report import ModelCallStats


class CatalogFixturePlanner:
    """Build a synthetic reply from public relations; no task labels or reference recipe."""

    def __init__(self):
        self.stats = ModelCallStats()

    async def generate(self, context):
        started = time.perf_counter()
        policies = {p["id"]: p for p in context["policies"]}
        goals = {g["id"]: g for g in context["project_goal_catalog"]["goals"]}
        defaults = {
            (d["policy_id"], d["location"], d["name"]): d["value"]
            for d in context["synthetic_input_defaults"]
        }
        steps, built, active = [], {}, set()

        def visit(policy_id, role):
            key = (policy_id, role)
            if key in built:
                return built[key]
            if key in active:
                raise ValueError("Fixture dependency cycle")
            active.add(key)
            policy = policies[policy_id]
            for auth in context["authentication"]:
                if (
                    auth["role"] == role
                    and auth["login_operation_id"]
                    and policy["operation_id"] in auth["before_operation_ids"]
                ):
                    options = [
                        p
                        for p in policies.values()
                        if p["operation_id"] == auth["login_operation_id"] and role in p["roles"]
                    ]
                    if len(options) != 1:
                        raise ValueError("Fixture authentication ambiguous")
                    visit(options[0]["id"], role)
            step = Step(
                id="fixture_" + str(len(steps) + 1),
                operation_id=policy["operation_id"],
                role=role,
                policy_id=policy_id,
            )
            for rule in policy["binding_rules"]:
                if rule["source_policy_id"]:
                    source = policies[rule["source_policy_id"]]
                    roles = [
                        r
                        for r in source["roles"]
                        if dependency_roles_match(r, role, rule["role_relation"])
                    ]
                    preferred = {
                        g["role"]
                        for g in goals.values()
                        if g["policy_id"] == source["id"] and g["role"] in roles
                    }
                    selected = next(iter(preferred)) if len(preferred) == 1 else roles[0]
                    producer = visit(source["id"], selected)
                    binding = ArgumentBinding(from_step=producer.id, select=rule["source_selector"])
                else:
                    binding = ArgumentBinding(
                        value=defaults[(policy_id, rule["location"], rule["name"])]
                    )
                getattr(step, rule["location"])[rule["name"]] = binding
            # A prerequisite may have occupied the provisional ID.
            step.id = "fixture_" + str(len(steps) + 1)
            steps.append(step)
            active.remove(key)
            built[key] = step
            return step

        seen = set()
        for goal_id in context["requested_goals"]:
            goal = goals[goal_id]
            alternatives = [v["policy_id"] for v in goal["variants"] if v["after_goal"] in seen]
            visit(alternatives[0] if alternatives else goal["policy_id"], goal["role"])
            seen.add(goal_id)
        self.stats = ModelCallStats(
            calls=1,
            model_name="SIMULATED",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            reason="SIMULATED",
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
        return Scenario(name="offline protocol fixture", steps=steps).model_dump_json()


def mock_adapters():
    """Exercise the real JSON adapter against MockTransport, not just a direct DSL fixture."""
    from agenttest.evidence_context import EvidenceFeedbackModel
    from agenttest.feedback import FakeFeedback
    from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner

    fixture, feedback = CatalogFixturePlanner(), FakeFeedback()
    settings = ModelSettings(
        model="SIMULATED",
        base_url="https://mock-provider.invalid/v1",
        api_key="offline-provider-credential-932be",
        timeout=60,
        max_input_tokens=25_000,
        max_output_tokens=4096,
        max_total_tokens=100_000,
    )

    async def planner_response(request):
        context = json.loads(json.loads(request.content)["messages"][-1]["content"])
        plan = Scenario.model_validate_json(await fixture.generate(context))
        draft = dict(
            name=plan.name,
            steps=[
                dict(
                    id=s.id,
                    operation_id=s.operation_id,
                    role=s.role,
                    policy_id=s.policy_id,
                    arguments=[
                        dict(
                            location=loc,
                            name=name,
                            value=b.value if b.from_step is None else None,
                            from_step=b.from_step,
                            select=b.select if b.from_step else None,
                        )
                        for loc in ("body", "path_parameters", "query")
                        for name, b in getattr(s, loc).items()
                    ],
                )
                for s in plan.steps
            ],
        )
        return completion(json.dumps(draft))

    async def feedback_response(request):
        context = json.loads(json.loads(request.content)["messages"][-1]["content"])
        return completion(await feedback.generate(context))

    def completion(content):
        return httpx.Response(
            200,
            json=dict(
                choices=[dict(finish_reason="stop", message=dict(content=content))],
                usage=dict(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            ),
        )

    return (
        OpenAICompatiblePlanner(
            settings,
            httpx.MockTransport(planner_response),
            profile="generic",
            planning_constraints=True,
        ),
        EvidenceFeedbackModel(settings, httpx.MockTransport(feedback_response)),
    )
