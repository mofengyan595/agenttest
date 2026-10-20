"""Operator-only service startup; browser requests cannot choose runtime configuration."""

import os
from pathlib import Path

from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.report import ReportStore
from agenttest.task_service import TaskService, load_projects
from agenttest.task_store import TaskStore


def configured_task_service(runs_dir):
    projects = load_projects(Path(os.environ["AGENTTEST_PROJECTS_FILE"]))

    def settings_for(project):
        path = os.environ.get("AGENTTEST_MODEL_ENV")
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

    return TaskService(projects, TaskStore(runs_dir / "tasks.sqlite3"),
        ReportStore(runs_dir), planner,
        synthetic=os.environ.get("AGENTTEST_SYNTHETIC_TARGETS") == "1",
        analyzer_factory=lambda project: feedback(settings_for(project)))


def feedback(settings):
    from agenttest.evidence_context import EvidenceFeedbackModel

    return EvidenceFeedbackModel(settings)
