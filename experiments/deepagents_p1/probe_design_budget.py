"""One real design call with the harness arguments: is ``MODEL_TOKEN_BUDGET`` real or an artefact?

Arm B's first run reported ``PlannerFailure / MODEL_TOKEN_BUDGET`` for every design attempt,
including a one-operation scope. Preparing that envelope is offline work, so this script reuses
the production planner and prints the exact size it rejected before spending another model call.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ab_runner import project, settings  # noqa: E402

from agenttest.llm_planner import ModelSettings, PlannerFailure  # noqa: E402
from agenttest.product_design import TestDesignModel, design_groups, propose_design  # noqa: E402


async def main(model: str) -> int:
    proj, _, _ = project("http://127.0.0.1:18991")
    loaded = settings()
    loaded = ModelSettings.model_validate({**loaded.model_dump(), "model": model})
    designer = TestDesignModel(loaded, profile="generic")
    group = design_groups(proj, operation_ids=["readAvailability"])[0]
    print(json.dumps(dict(group=group["id"], primary=group["primary"],
                          closure=group["operations"]), ensure_ascii=False))
    try:
        cases = await propose_design(proj, "帮我测一下包裹管理功能。", group, designer,
            environ={}, remaining_requests=24,
            inventory_total=len(proj.contract.operations),
            normal_operation_ids=group["primary"], must_be_relevant=True)
        print("CASES:", len(getattr(cases, "cases", []) or []))
        print("USAGE:", designer.stats.model_dump(mode="json"))
        return 0
    except PlannerFailure as failure:
        diagnostic = failure.diagnostic
        print("FAILURE:", diagnostic.code if diagnostic else failure.args)
        print("USAGE:", designer.stats.model_dump(mode="json"))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "ecnu-max")))
