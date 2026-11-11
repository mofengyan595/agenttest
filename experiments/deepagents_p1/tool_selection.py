"""Tool-selection bench: does the harness pick the narrowest useful tool for a stated need?

Each case states a need in natural language and names the tool the work must start with. The
model runs *for real* over the eight-tool harness, but every tool is a recorder: nothing here
touches HTTP, the filesystem or the model's ability to act outside the catalog. A case passes
only when the first tool the model calls is the expected one -- selecting `design_test_cases`
straight away, for instance, is wrong when the contract has not been read yet.

    .\\.venv-deepagents\\Scripts\\python.exe experiments/deepagents_p1/tool_selection.py
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

CASES = [
    dict(need="我不知道这个项目里有哪些接口可以测。", expect="get_contract"),
    dict(need="我想知道哪些状态码是操作员已经批准过的断言。", expect="get_approved_policies"),
    dict(need="先看看这轮还剩多少请求预算，别再超了。", expect="get_test_coverage"),
    dict(need="请为 createParcel 和 readParcel 设计两个场景。", expect="design_test_cases"),
    dict(need="这是我刚设计好的设计 design_1，先做可执行性预检。", expect="validate_test_plan"),
    dict(need="plan design_1_plan_1 已经通过预检了，现在执行它。", expect="execute_approved_test"),
    dict(need="刚才那次运行 936ec854dd1f451a9bc5ae258c9a7a47 到底哪一步失败了？",
         expect="read_test_report"),
    dict(need="这个 503 是稳定复现还是偶发？请做只读复核。", expect="investigate_failure"),
]


async def one(model, case: dict, index: int) -> dict:
    from deepagents import create_deep_agent
    from langchain_core.tools import StructuredTool

    from agenttest.deep_harness.loop import HARNESS_TOOL_NAMES, SYSTEM_PROMPT

    called: list[str] = []

    def make(name: str):
        async def reply() -> dict:
            called.append(name)
            return {"ok": True, "note": "离线记录，无副作用"}

        return StructuredTool.from_function(coroutine=reply, name=name,
                                            description=_descriptions()[name])

    tools = [make(name) for name in HARNESS_TOOL_NAMES]
    agent = create_deep_agent(model=model, tools=tools, system_prompt=SYSTEM_PROMPT)
    try:
        await agent.ainvoke({"messages": [{"role": "user", "content": case["need"]}]},
                            config={"recursion_limit": 12})
    except Exception as exc:  # a loop that never stops is a result too, not a crash
        called.append(f"<{type(exc).__name__}>")
    first = called[0] if called else None
    return dict(need=case["need"], expected=case["expect"], first=first,
                calls=called, passed=first == case["expect"],
                catalog=sorted(HARNESS_TOOL_NAMES))


def _descriptions() -> dict:
    return {
        "get_contract": "读取已批准的操作目录。",
        "get_approved_policies": "读取已批准的断言与资源生命周期规则。",
        "get_test_coverage": "读取已测量的覆盖与请求预算。",
        "design_test_cases": "请设计模型针对已批准操作提出场景（不发请求）。",
        "validate_test_plan": "对场景执行确定性预检，返回可执行 plan_id。",
        "execute_approved_test": "执行一个已通过预检的 plan_id。",
        "read_test_report": "读取某次运行的报告与检查证据。",
        "investigate_failure": "对失败步骤做只读复核。",
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path(".eduwork/runs/p1/tool-selection.json"))
    args = parser.parse_args()

    from agenttest.deep_harness.chat_model import build_chat_model
    from agenttest.deep_harness.loop import install_narrow_profile
    from agenttest.llm_planner import ModelSettings

    settings = ModelSettings.from_env_file(ROOT / ".env")
    model = build_chat_model(settings)
    install_narrow_profile(model)

    rows = [await one(model, case, index) for index, case in enumerate(CASES)]
    passed = sum(1 for row in rows if row["passed"])
    payload = dict(probe="tool_selection", model=settings.model,
                   passed=passed, total=len(rows), rows=rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in rows:
        mark = "PASS" if row["passed"] else "FAIL"
        print(f"{mark} {row['need'][:34]:<36} expected={row['expected']:<22} first={row['first']}")
    print(f"tool selection: {passed}/{len(rows)}  evidence: {args.out}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
