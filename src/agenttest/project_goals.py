"""Reusable operator-owned goal vocabulary; no per-request recipe or model authority."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from agenttest.contracts import ContractSummary, load_openapi
from agenttest.credentials import CredentialVault
from agenttest.discovery import trusted
from agenttest.planner_input import validate_user_task
from agenttest.policies import PolicyRegistry
from agenttest.project import ProjectConfig, _read
from agenttest.report import fingerprint
from agenttest.safety import StrictModel, TargetConfig


class GoalVariant(StrictModel):
    after_goal: str
    policy_id: str


class GoalTemplate(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    aliases: list[str] = Field(min_length=1, max_length=12)
    policy_id: str
    role: str | None
    # A policy's approved state transition changes the applicable read assertion.
    variants: list[GoalVariant] = Field(default_factory=list, max_length=8)


class GoalCatalog(StrictModel):
    schema_version: Literal["agenttest.goal-catalog.v1"] = "agenttest.goal-catalog.v1"
    resource_aliases: list[str] = Field(min_length=1, max_length=12)
    goals: list[GoalTemplate] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unambiguous(self):
        ids = [g.id for g in self.goals]
        aliases = [a for g in self.goals for a in g.aliases]
        if len(set(ids)) != len(ids) or len(set(aliases)) != len(aliases):
            raise ValueError("Duplicate goal vocabulary")
        for text in [*aliases, *self.resource_aliases]:
            if len(text) > 80 or validate_user_task(text) != text:
                raise ValueError("Invalid public goal vocabulary")
        for goal in self.goals:
            if (len({v.after_goal for v in goal.variants}) != len(goal.variants)
                    or any(v.after_goal not in ids or v.after_goal == goal.id
                           for v in goal.variants)):
                raise ValueError("Invalid state relation")
        return self


class ProjectBudget(StrictModel):
    max_model_calls: int = Field(default=4, ge=1, le=4)
    max_readonly_investigations: int = Field(default=2, ge=0, le=2)
    max_input_tokens: int = Field(default=20_000, ge=1, le=100_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=16_000)
    max_total_tokens: int = Field(default=25_000, ge=1, le=120_000)
    suite_max_model_calls: int = Field(default=8, ge=1, le=20)
    suite_max_total_tokens: int = Field(default=100_000, ge=1, le=500_000)


class RegisteredProject(StrictModel):
    schema_version: Literal["agenttest.registered-project.v1"]
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    contract: str
    target: str
    policies: str
    goals: str
    budget: ProjectBudget = Field(default_factory=ProjectBudget)

    @field_validator("contract", "target", "policies", "goals")
    @classmethod
    def local_reference(cls, value):
        return ProjectConfig.local_reference(value)


@dataclass(repr=False)
class GoalProject:
    registration: RegisteredProject
    document: bytes
    contract: ContractSummary
    target: TargetConfig
    registry: PolicyRegistry
    catalog: GoalCatalog

    def identity(self) -> str:
        return fingerprint(dict(project=self.registration.model_dump(),
            contract=self.contract.model_dump(), target=self.target.model_dump(),
            policies=self.registry.digest(), goals=self.catalog.model_dump()))


def load_goal_project(path: Path) -> GoalProject:
    """Load local operator configuration, never an uploaded or planner-authored path."""
    path = path.absolute()
    if ".." in path.parts:
        raise ValueError("PROJECT_INPUT_REJECTED")
    registration = RegisteredProject.model_validate_json(_read(path, 16_000))
    document = _read(path.parent / registration.contract)
    project = GoalProject(registration, document, load_openapi(document),
        TargetConfig.model_validate_json(_read(path.parent / registration.target)),
        PolicyRegistry.from_json(_read(path.parent / registration.policies)),
        GoalCatalog.model_validate_json(_read(path.parent / registration.goals)))
    operations = {o.operation_id: o for o in project.contract.operations if o.operation_id}
    policies = {p.id: p for p in project.registry.definitions()}
    for goal in project.catalog.goals:
        for policy_id in [goal.policy_id, *[v.policy_id for v in goal.variants]]:
            policy = policies.get(policy_id)
            if (policy is None or not trusted(policy)
                    or policy.business_scenario != project.target.business_scenario
                    or policy.operation_id not in operations
                    or goal.role not in policy.roles
                    or goal.role is not None and goal.role not in project.target.roles):
                raise ValueError("PROJECT_GOAL_NOT_APPROVED")
            # Disallowed target operations remain catalogued for explicit review reporting.
    if set(project.target.allowed_operations) - operations.keys():
        raise ValueError("PROJECT_OPERATION_UNKNOWN")
    return project


def check_goal_credentials(project: GoalProject, task, environ: dict[str, str]) -> None:
    """Check only roles actually required by the derived request, before model access."""
    vault = CredentialVault(project.target, environ)
    try:
        if (vault.contains_secret(task.user_task)
                or vault.contains_secret(project.catalog.model_dump())):
            raise ValueError("PROJECT_PUBLIC_INPUT_CONTAINS_CREDENTIAL")
    finally:
        vault.close()
    for step in task.requirements.steps:
        role = project.target.roles.get(step.role)
        if role is None:
            continue
        names = ((role.username_env, role.password_env) if role.login_operation
                 else (role.bearer_env,))
        if any(not name or not environ.get(name) or any(c in environ[name] for c in "\r\n")
               for name in names):
            raise ValueError("PROJECT_REQUIRED_CREDENTIAL_MISSING")
