"""Natural-language mini-target composition; all HTTP execution stays in Executor."""

import json
import secrets

import httpx

from agenttest.demo import PROJECT_ROOT, demo_inputs
from agenttest.mini_target import Fault, create_target
from agenttest.policies import PolicyRegistry
from agenttest.report import ReportStore, RunReport


def natural_inputs(fault: Fault = "none"):
    _, config = demo_inputs()
    config.business_scenario = "mini_target_nl_v1"
    config.allowed_operations = ["login", "createCourse", "getCourse"]
    environ = {
        "DEMO_ALICE_USERNAME": "alice", "DEMO_ALICE_PASSWORD": secrets.token_urlsafe(24),
        "DEMO_BOB_USERNAME": "bob", "DEMO_BOB_PASSWORD": secrets.token_urlsafe(24),
    }
    target = create_target({"alice": environ["DEMO_ALICE_PASSWORD"],
                            "bob": environ["DEMO_BOB_PASSWORD"]}, fault)
    registry = PolicyRegistry.from_json((PROJECT_ROOT / "examples/nl-policies.json").read_bytes())
    return target, config, registry, environ


async def run_natural_demo(user_task: str, planner, store: ReportStore | None = None,
                           *, fault: Fault = "none", budget=None, investigation=None,
                           capabilities=None, analyzer=None) -> RunReport:
    from agenttest.workflow import Workflow

    target, config, registry, environ = natural_inputs(fault)
    return await Workflow(json.dumps(target.openapi()).encode(), config, registry, planner,
                          store, httpx.ASGITransport(target, raise_app_exceptions=False),
                          environ, budget, user_task=user_task, investigation=investigation,
                          capabilities=capabilities, analyzer=analyzer).run()
