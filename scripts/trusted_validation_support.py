"""Synthetic runtime wiring and public-only model tracing for the new frozen evaluation."""

import json
import runpy
from pathlib import Path

import httpx

from agenttest.demo import PROJECT_ROOT
from agenttest.dsl import Scenario
from agenttest.evidence_context import EvidenceFeedbackModel
from agenttest.feedback import FakeFeedback
from agenttest.goal_spec import GoalSpecModel
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.nl_entry_evaluation import TraceModel
from agenttest.nl_entry_fixture import CatalogFixturePlanner
from agenttest.portability_demo import project_inputs

PROFILES = {
    "courses": PROJECT_ROOT / "examples/trusted-testing/projects/courses",
    "tickets": PROJECT_ROOT / "examples/trusted-testing/projects/tickets",
    "tickets_limited": PROJECT_ROOT / "examples/trusted-testing/projects/tickets_limited",
    "courses_limited": PROJECT_ROOT / "examples/trusted-testing/projects/courses_limited",
    "laboratory": PROJECT_ROOT / "examples/trusted-testing/laboratory",
}
CONFIG_FILES = ("openapi.json", "target.json", "policies.json", "goals.json",
                "registered.project.json", "capabilities.json")


def target_runtime(project, fault="none"):
    """Harness-selected fixture only; neither labels nor fixture switches reach a model."""
    if project.registration.id == "laboratory":
        return runpy.run_path(str(PROFILES["laboratory"] / "fixture.py"))["runtime"](fault)
    app, _, _, _, env = project_inputs(project.registration.id, fault)
    return app, env


class ObservedASGI(httpx.ASGITransport):
    def __init__(self, app):
        super().__init__(app=app)
        self.requests = 0

    async def handle_async_request(self, request):
        self.requests += 1
        return await super().handle_async_request(request)


class PublicTrace(TraceModel):
    async def generate(self, context):
        pending = [context]
        forbidden = {"expected_groups", "score", "eligible", "designated_failure"}
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if forbidden & value.keys():
                    raise ValueError("SCORING_INFORMATION_ENTERED_RUNTIME")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return await super().generate(context)


def mock_models(protocol="openai_compatible"):
    """Exercise the same JSON adapter with fixed synthetic outputs and simulated usage."""
    fixture, feedback = CatalogFixturePlanner(), FakeFeedback()
    settings = ModelSettings(model="SIMULATED", base_url="https://mock-provider.invalid/v1",
        api_key="offline-fixture-credential", protocol=protocol, timeout=60,
        max_input_tokens=20000, max_output_tokens=4096, max_total_tokens=100000)

    def completion(content):
        return httpx.Response(200, json=dict(choices=[dict(finish_reason="stop",
            message=dict(content=content))], usage=dict(prompt_tokens=100,
                completion_tokens=50, total_tokens=150)))

    def context(request):
        return json.loads(json.loads(request.content)["messages"][-1]["content"])

    async def plan_reply(request):
        plan = Scenario.model_validate_json(await fixture.generate(context(request)))
        return completion(json.dumps(dict(name=plan.name, steps=[dict(id=s.id,
            operation_id=s.operation_id, role=s.role, policy_id=s.policy_id, arguments=[dict(
                location=loc, name=name, value=b.value if not b.from_step else None,
                from_step=b.from_step, select=b.select if b.from_step else None)
                for loc in ("body", "path_parameters", "query")
                for name, b in getattr(s, loc).items()]) for s in plan.steps])))

    async def feedback_reply(request):
        return completion(await feedback.generate(context(request)))

    async def semantic_reply(request):
        text = context(request)["original_request"]
        return completion(json.dumps(dict(scope="unknown", goals=[],
                                         unresolved=[dict(start=0, end=len(text))])))

    return (OpenAICompatiblePlanner(settings, httpx.MockTransport(plan_reply), profile="generic",
                                   planning_constraints=True),
            EvidenceFeedbackModel(settings, httpx.MockTransport(feedback_reply)),
            GoalSpecModel(settings, httpx.MockTransport(semantic_reply), profile="generic"))


def model_stats(report):
    return ([report.model_call] if report.model_call else []) + (
        [d.model_call for d in report.feedback.decisions if d.model_call]
        if report.feedback else [])


def copied_profiles(output):
    for key, original in PROFILES.items():
        dest = Path(output) / key
        dest.mkdir(parents=True, exist_ok=False)
        for name in CONFIG_FILES:
            (dest / name).write_bytes((original / name).read_bytes())
