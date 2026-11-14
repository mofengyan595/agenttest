"""Run one P3-A ablation cell from an unpacked 9cb8715 source archive."""

import argparse
import json
import sys
from pathlib import Path

BASELINE = "9cb871507afb88e0039bf64b3f85d26b34cc0ed8"


def configure(arm):
    from langchain.agents import middleware

    from agenttest import product_agent

    run_agent = product_agent.run_agent

    async def configured(*args, **kwargs):
        return await run_agent(*args, **kwargs, enable_skills=arm in "BD")

    product_agent.run_agent = configured
    if arm in "AC":
        product_agent.SYSTEM_PROMPT = product_agent.SYSTEM_PROMPT.replace(
            "先根据原始目标选读一个相关 Skill，然后调用\n",
            "先调用\n").replace(
            "Skill 指导测试方法，授权、断言和完成判据以工具为准。\n"
            "缺少适用 Skill 时，仍按", "授权、断言和完成判据以工具为准。\n按")
    if arm in "AB":
        # Keep the bounded P2.1 cleaner, trusted evidence, pagination and refusals.
        product_agent.AgentSession.read_view = (
            lambda self, name, build, *, refresh=False: build())
        edit = middleware.ClearToolUsesEdit

        def p21_edit(**kwargs):
            kwargs.pop("exclude_tools", None)
            return edit(**kwargs)

        middleware.ClearToolUsesEdit = p21_edit
        serialize = product_agent._json

        def spaced(payload):
            wire = serialize(payload)
            view = json.dumps(json.loads(wire), ensure_ascii=False)
            return view if len(view.encode()) <= product_agent.MAX_RESULT_BYTES else wire

        product_agent._json = spaced


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--arm", choices=list("ABCD"), required=True)
    parser.add_argument("--startup-seconds", type=float, default=10)
    args, remaining = parser.parse_known_args()
    source = args.source.resolve()
    if not (source / "p3a-baseline.txt").is_file() or (
            source / "p3a-baseline.txt").read_text(encoding="utf-8").strip() != BASELINE:
        parser.error("Use an archive of the P3-A baseline, marked with its full SHA.")
    sys.path[:0] = [str(source / "src"), str(source / "scripts")]
    import run_development_agent as runner

    health = runner.wait_health
    runner.wait_health = lambda port: health(port, args.startup_seconds)

    configure(args.arm)
    sys.argv[1:] = remaining
    runner.main()


if __name__ == "__main__":
    main()
