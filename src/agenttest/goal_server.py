"""Operator-only service startup; browser requests cannot choose runtime configuration."""

import json
import os
from pathlib import Path

from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import ReportStore
from agenttest.task_service import TaskService, load_projects
from agenttest.task_store import TaskStore


def target_environment(path, environ=None):
    """Read only literal target aliases; never import shell variables or execute .env text."""
    values = {}
    if path and Path(path).is_file():
        with Path(path).open("rb") as source:
            raw = source.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("TARGET_ENVIRONMENT_TOO_LARGE")
        for line in raw.decode("utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            name, separator, value = line.partition("=")
            name = name.strip()
            if not name.startswith("AGENTTEST_TARGET_"):
                continue
            if not separator or name in values:
                raise ValueError("TARGET_ENVIRONMENT_INVALID")
            value = value.strip()
            if value.startswith(("'", '"')):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError("TARGET_ENVIRONMENT_INVALID")
                value = value[1:-1]
            else:
                value = value.split(" #", 1)[0].rstrip()
            if "${" in value or any(c in value for c in "\r\n\x00"):
                raise ValueError("TARGET_ENVIRONMENT_INVALID")
            values[name] = value
    values.update(os.environ if environ is None else environ)
    return values


def configured_task_service(runs_dir, *, projects=None, model_env=None, generic_intake=False):
    if projects is None:
        registry = os.environ.get("AGENTTEST_PROJECTS_FILE")
        projects = load_projects(Path(registry)) if registry else {}

    def settings_for(project):
        path = model_env or os.environ.get("AGENTTEST_MODEL_ENV")
        settings = (ModelSettings.from_env_file(Path(path)) if path
                    else ModelSettings.from_environment())
        return settings.model_copy(update={key: getattr(project.registration.budget, key)
            for key in ("max_input_tokens", "max_output_tokens", "max_total_tokens")})

    def planner(project):
        settings = settings_for(project)
        if project.registration.native_function_calling:
            from agenttest.native_tools import NativeFunctionPlanner

            return NativeFunctionPlanner(settings, provider_supported=True)
        return OpenAICompatiblePlanner(settings, profile="generic", planning_constraints=True)

    def model_status():
        try:
            path = model_env or os.environ.get("AGENTTEST_MODEL_ENV")
            ModelSettings.from_env_file(Path(path)) if path else ModelSettings.from_environment()
            return "CONFIGURED_MODEL"
        except (ValueError, OSError):
            return "UNAVAILABLE"

    def private_input_guard(value):
        try:
            path = model_env or os.environ.get("AGENTTEST_MODEL_ENV")
            settings = (ModelSettings.from_env_file(Path(path)) if path
                        else ModelSettings.from_environment())
        except (ValueError, OSError):
            return False
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return settings.api_key.get_secret_value() in text

    return TaskService(projects, TaskStore(runs_dir / "tasks.sqlite3"),
        ReportStore(runs_dir), planner,
        synthetic=not generic_intake and os.environ.get("AGENTTEST_SYNTHETIC_TARGETS") == "1",
        analyzer_factory=lambda project: feedback(settings_for(project)),
        semantic_factory=lambda project: goal_spec_model(settings_for(project)),
        designer_factory=lambda project: test_designer(settings_for(project)),
        focus_factory=lambda project: test_focus(settings_for(project)),
        model_status=model_status, generic_intake=generic_intake,
        private_input_guard=private_input_guard,
        environ=target_environment(model_env or os.environ.get("AGENTTEST_MODEL_ENV")),
        intake_authorized_ips=tuple(filter(None,
            os.environ.get("AGENTTEST_INTAKE_AUTHORIZED_IPS", "").split(","))))


def feedback(settings):
    from agenttest.evidence_context import EvidenceFeedbackModel

    return EvidenceFeedbackModel(settings)


def goal_spec_model(settings):
    from agenttest.goal_spec import GoalSpecModel

    return GoalSpecModel(settings, profile="generic")


def test_designer(settings):
    from agenttest.product_design import TestDesignModel

    return TestDesignModel(settings.model_copy(update={"timeout": 120.0}), profile="generic")


def test_focus(settings):
    from agenttest.product_focus import TestFocusModel

    return TestFocusModel(settings.model_copy(update={"timeout": 90.0}), profile="generic")
