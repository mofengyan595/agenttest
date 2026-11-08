"""Identical operator-side capacity cap for both frozen product versions."""


def cap(project):
    budget = project.registration.budget
    assert budget.suite_max_model_calls >= 4
    # Keep native per-call/aggregate token limits: the engine reserves input BYTE bounds.
    project.registration.budget = budget.model_copy(update={"suite_max_model_calls": 4})
    return project


def install():
    from agenttest import project_intake
    from agenttest.task_store import TaskStore

    original = project_intake.build_generic_project

    def capped(*args, **kwargs):
        return cap(original(*args, **kwargs))

    project_intake.build_generic_project = capped
    original_create = TaskStore.create

    def create(self, task_id, owner, submission, project, identity, request_sha, state, summary):
        if summary.get("mode") == "MODEL_DESIGNED":
            budget = summary.get("budget", {})
            budget["max_seconds"] = min(budget.get("max_seconds", 600), 180)
        return original_create(
            self, task_id, owner, submission, project, identity, request_sha, state, summary
        )

    TaskStore.create = create
