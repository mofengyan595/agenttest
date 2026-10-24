"""Run one frozen position against a plain source copy; evaluator stays outside runtime."""

import argparse
import asyncio
import hashlib
import inspect
import json
import sys
from pathlib import Path


def source_files(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("src", "scripts", "examples")
            for p in sorted((root / folder).rglob("*.py"))
            if "__pycache__" not in p.parts}


def run(args):
    # Must happen before any AgentTest import; editable installation cannot select the arm.
    sys.path.insert(0, str(args.source / "src"))
    from evaluate_trusted_testing import position

    import agenttest
    from agenttest.dsl import Scenario
    from agenttest.eval_v2.archive import inventory
    from agenttest.evaluation import save_evaluation
    from agenttest.evaluation_freeze import public_model_config
    from agenttest.goal_runtime import GoalPlanner
    from agenttest.goal_spec import GoalSpecModel
    from agenttest.goal_suite import build_suite, run_suite
    from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
    from agenttest.planner_input import planning_context
    from agenttest.project_goals import load_goal_project
    from agenttest.report import fingerprint

    job = json.loads(args.job.read_text(encoding="utf-8"))
    source = args.source.absolute()
    if source not in Path(agenttest.__file__).absolute().parents:
        raise ValueError("WRONG_PRODUCT_SOURCE_IMPORTED")
    expected = job["source_files"]
    profile_inventory = inventory(args.folder.parent / "projects")

    def guard():
        if (source_files(source) != expected
                or source_files(Path(__file__).resolve().parents[1]) != job["harness_files"]
                or inventory(args.folder.parent / "projects") != profile_inventory):
            raise ValueError("FROZEN_SOURCE_OR_PROJECT_CHANGED")

    guard()
    if args.model_env:
        settings = ModelSettings.from_env_file(args.model_env)
    elif job.get("model_config"):
        config = job["model_config"]
        settings = ModelSettings(model=config["model"], base_url=job["model_endpoint"],
            protocol=config["protocol"], api_key="offline-preflight-credential")
    else:
        settings = ModelSettings.from_environment({"AGENTTEST_MODEL": "ecnu-plus",
            "OPENAI_BASE_URL": "https://chat.ecnu.edu.cn/open/api/v1",
            "OPENAI_API_KEY": "offline-preflight-credential"})
    settings = settings.model_copy(update=dict(timeout=60.0, max_input_tokens=20000,
        max_output_tokens=4096, max_total_tokens=100000))
    public_settings = public_model_config(settings)
    if job.get("model_config") and public_settings != job["model_config"]:
        raise ValueError("FROZEN_MODEL_SETTINGS_CHANGED")
    if args.preflight:
        project = load_goal_project(args.folder.parent / "projects" /
            job["request"]["project"] / "registered.project.json")
        suite = build_suite(project, job["request"]["user_task"])
        adapter = OpenAICompatiblePlanner(settings, profile="generic", planning_constraints=True)
        encoded = []

        class EncodeOnly:
            async def generate(self, context):
                adapter.prepare_request(context)
                encoded.append(dict(request_sha256=adapter.stats.request_sha256,
                    request_bytes=adapter.stats.request_bytes,
                    input_token_upper_bound=adapter.stats.input_token_upper_bound))
                return ""

        reuse = "on_validated_plan" in inspect.signature(run_suite).parameters
        for case in suite.cases:
            if not case.goal or not case.goal.task or reuse and case.is_derived:
                continue
            context = planning_context(project.contract, project.target, project.registry,
                                       job["request"]["user_task"], Scenario.model_json_schema())
            asyncio.run(GoalPlanner(EncodeOnly(), project, case.goal).generate(context))
        if not encoded:
            semantic = GoalSpecModel(settings)
            semantic.prepare_request(dict(original_request=job["request"]["user_task"],
                project_id=project.registration.id, catalog=project.catalog.model_dump()))
            encoded.append(dict(request_sha256=semantic.stats.request_sha256,
                request_bytes=semantic.stats.request_bytes,
                input_token_upper_bound=semantic.stats.input_token_upper_bound))
        result = dict(source_import=str(Path(agenttest.__file__).absolute()),
            source_sha256=fingerprint(expected), profile_identity=project.identity(),
            public_model_config=public_settings, encoded=encoded,
            scope=suite.scope, preview=suite.preview(), new_model_calls=0, http_requests=0)
    else:
        result = asyncio.run(position(args.folder, job["request"], job["fault"],
            settings if args.model_env else None, guard))
    guard()
    save_evaluation(result, args.result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--model-env", type=Path)
    run(parser.parse_args())
