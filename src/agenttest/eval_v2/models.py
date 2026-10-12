"""Operator-owned labels are a separate type from the public planning request."""

from typing import Any, Literal

from pydantic import Field, model_validator

from agenttest.eval_v2.review import ReviewRecord
from agenttest.plan_json import bounded_json
from agenttest.planner_input import validate_user_task
from agenttest.safety import StrictModel
from agenttest.selectors import validate_selector

Category = Literal[
    "single_api",
    "dependencies",
    "authentication",
    "boundaries",
    "error_status",
    "consistency",
    "async",
    "unsupported",
    "injection",
]
Source = Literal["manual", "fake", "mock_transport", "real"]
REJECTION_CODES = frozenset({
    "MODEL_NO_APPROVED_PLAN", "MODEL_REFUSAL", "INTENT_UNSUPPORTED_OBJECTIVE",
    "INTENT_UNSUPPORTED_LANGUAGE", "INTENT_EXPLICIT_NAME_REQUIRED", "INTENT_CREATOR_AMBIGUOUS",
    "INTENT_READ_REQUIRED", "INTENT_NAME_UNSUPPORTED", "INTENT_ROLE_AMBIGUOUS",
    "INTENT_RELATION_AMBIGUOUS",
})
ResultStatus = Literal[
    "PASS",
    "TASK_MISMATCH",
    "PLAN_INVALID",
    "INTENT_MISMATCH",
    "ASSERTION_FAILED",
    "TOOL_ERROR",
    "TEST_INFRA_ERROR",
    "NEEDS_REVIEW",
    "SAFE_REJECTION",
    "UNSUPPORTED",
    "NOT_ATTEMPTED_BUDGET",
    "MODEL_USAGE_UNAVAILABLE",
    "EVALUATION_CONFIGURATION_ERROR",
]


class PublicTask(StrictModel):
    user_task: str
    target_profile_id: Literal["mini_nl_v1"] = "mini_nl_v1"
    contract_ref: Literal["mini_target.openapi"] = "mini_target.openapi"
    policy_registry_ref: Literal["examples/nl-policies.json"] = "examples/nl-policies.json"
    fixture_version: Literal["mini-nl-v1"] = "mini-nl-v1"

    @model_validator(mode="after")
    def safe_task(self):
        validate_user_task(self.user_task)
        return self


class ExpectedArgument(StrictModel):
    location: Literal["body", "query", "path_parameters"]
    name: str = Field(min_length=1, max_length=80)
    value: Any = None
    source: str | None = None  # A rubric step key, never a generated step ID.
    selector: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if self.source is None:
            if "value" not in self.model_fields_set or self.selector is not None:
                raise ValueError("Literal label requires an explicit value")
        elif self.value is not None or self.selector is None:
            raise ValueError("Dependency label requires a selector and no literal")
        if self.selector is not None:
            validate_selector(self.selector)
        return self


class ExpectedStep(StrictModel):
    key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
    operation_id: str
    role: str | None
    policy_id: str
    arguments: list[ExpectedArgument] = Field(default_factory=list, max_length=30)
    goals: list[str] = Field(min_length=1)


class Rubric(StrictModel):
    steps: list[ExpectedStep] = Field(min_length=1, max_length=20)
    before: list[tuple[str, str]] = Field(default_factory=list)
    required_goals: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def coherent(self):
        keys = [s.key for s in self.steps]
        signatures = [(s.operation_id, s.role, s.policy_id) for s in self.steps]
        if len(set(keys)) != len(keys) or len(set(signatures)) != len(signatures):
            raise ValueError("Rubric keys/signatures must be unambiguous")
        edges = list(self.before)
        goals = set()
        for step in self.steps:
            names = [(a.location, a.name) for a in step.arguments]
            if len(set(names)) != len(names):
                raise ValueError("Duplicate argument label")
            for arg in step.arguments:
                if arg.source is not None:
                    edges.append((arg.source, step.key))
            goals.update(step.goals)
        if set(self.required_goals) != goals:
            raise ValueError("Every goal must have structured evidence")
        pending = set(keys)
        if any(a not in pending or b not in pending or a == b for a, b in edges):
            raise ValueError("Unknown/self rubric dependency")
        while pending:
            ready = {key for key in pending if not any(b == key and a in pending for a, b in edges)}
            if not ready:
                raise ValueError("Cyclic rubric dependencies")
            pending -= ready
        return self


class Labels(StrictModel):
    support_status: Literal["supported", "unsupported", "needs_fixture", "review"]
    expected_behavior: Literal["execute", "reject", "review"]
    rubric: Rubric | None = None
    rejection_basis: list[str] = Field(default_factory=list)
    faults: list[
        Literal[
            "response_name",
            "response_id",
            "create_status",
            "ownership",
        ]
    ] = Field(default_factory=list, max_length=4)
    limitation: str = Field(min_length=1)
    review_status: Literal["UNREVIEWED", "HUMAN_REVIEWED"] = "UNREVIEWED"
    review_source: str | None = None
    label_provenance: Literal["ai_generated_draft", "developer", "independent_human"]

    @model_validator(mode="after")
    def complete_labels(self):
        if self.expected_behavior == "execute":
            if self.support_status != "supported" or self.rubric is None:
                raise ValueError("Executable case requires supported structured labels")
        elif self.rubric is not None or self.faults:
            raise ValueError("Non-executable case cannot carry execution labels")
        if self.expected_behavior == "reject" and not self.rejection_basis:
            raise ValueError("Rejection requires explicit independently reviewed basis")
        if self.expected_behavior == "reject" and self.support_status != "unsupported":
            raise ValueError("Rejection labels must explicitly be unsupported")
        if not set(self.rejection_basis) <= REJECTION_CODES:
            raise ValueError("Errors cannot be labeled as safe rejection")
        if self.review_status == "HUMAN_REVIEWED" and (
            not (self.review_source or "").strip() or self.label_provenance == "ai_generated_draft"
        ):
            raise ValueError("Generated labels cannot self-certify human review")
        if len(set(self.faults)) != len(self.faults):
            raise ValueError("Duplicate fault mechanism")
        return self


class Case(StrictModel):
    case_id: str = Field(pattern=r"^C[0-9]{3}$")
    category: Category
    task: PublicTask
    labels: Labels
    review_record: ReviewRecord | None = None


class Suite(StrictModel):
    schema_version: Literal["agenttest.eval.v2"] = "agenttest.eval.v2"
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    split: Literal["development", "independent_holdout"]
    origin: str = Field(min_length=1)
    cases: list[Case] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [c.case_id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate case ID")
        return self

    def require_formal(self):
        if self.split != "independent_holdout" or any(
            c.labels.review_status != "HUMAN_REVIEWED"
            or c.labels.label_provenance != "independent_human"
            for c in self.cases
        ):
            raise ValueError("Formal evaluation requires independently human-reviewed holdout")
        from agenttest.eval_v2.review import require_review

        require_review(self)

    @classmethod
    def load(cls, path):
        with path.open("rb") as stream:
            raw = stream.read(1_000_001)
        import json

        data = bounded_json(raw.decode("utf-8"), 1_000_000)
        return cls.model_validate_json(json.dumps(data))


class IntentScore(StrictModel):
    passed: bool
    diagnostics: list[str]
    covered_goals: list[str]


class StepEvidence(StrictModel):
    step_id: str
    operation_id: str
    role: str | None
    policy_id: str | None
    argument_sha256: dict[str, str]
    dependencies: dict[str, str]
    selectors: dict[str, str]
    upload: bool
    poll: bool
    read_retries: int


class PlanEvidence(StrictModel):
    scenario_sha256: str
    steps: list[StepEvidence]

    @model_validator(mode="after")
    def unambiguous(self):
        ids = [s.step_id for s in self.steps]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError("Plan evidence requires unique step IDs")
        for step in self.steps:
            if (
                set(step.argument_sha256) & set(step.dependencies)
                or set(step.selectors) != set(step.dependencies)
            ):
                raise ValueError("Ambiguous archived argument evidence")
        return self


class CaseResult(StrictModel):
    case_id: str
    category: Category
    source: Source
    variant: Literal["A", "B"]
    eligible: bool = True
    attempted: bool = False
    executed: bool = False
    classification: ResultStatus
    diagnostics: list[str] = Field(default_factory=list)
    plan_valid: bool = False
    intent: IntentScore | None = None
    end_to_end_success: bool = False
    safe_rejection: bool = False
    rejection_kind: str | None = None
    run_id: str | None = None
    report_sha256: str | None = None
    plan_sha256: str | None = None
    plan_evidence: PlanEvidence | None = None
    context_sha256: str | None = None
    context_bytes: int | None = None
    model_calls: int = 0
    planner_invocations: int = 0
    real_api_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    model_elapsed_ms: float | None = None
    workflow_elapsed_ms: float | None = None
    http_requests: int = 0
    http_attempt_elapsed_ms: float | None = None
    usage_status: Literal["NOT_APPLICABLE", "OBSERVED", "MODEL_USAGE_UNAVAILABLE"]
