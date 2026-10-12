"""Operator review records and immutable version locks; never certify human identity."""

import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from agenttest.evaluation import save_evaluation
from agenttest.plan_json import bounded_json
from agenttest.report import fingerprint
from agenttest.safety import StrictModel


class ReviewRecord(StrictModel):
    source_ref: str = Field(min_length=1)
    source_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sampled_at: str
    sampling_method: str = Field(min_length=1)
    participated_in_development: bool
    operator_independence_confirmed: bool
    reviewer_ref: str = Field(min_length=1)
    reviewed_at: str
    decision: Literal["execute", "reject", "review"]
    rationale: str = Field(min_length=1)
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_version: str
    acceptable_topology: str = Field(min_length=1)
    forbidden_extras: str = Field(min_length=1)
    rejection_evidence: list[str] = Field(default_factory=list)

    @field_validator("sampled_at", "reviewed_at")
    @classmethod
    def dated(cls, value):
        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Review timestamps require timezone")
        return value

    @field_validator(
        "source_ref", "sampling_method", "reviewer_ref", "rationale",
        "acceptable_topology", "forbidden_extras",
    )
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Review evidence must not be blank")
        return value


def task_identity(task):
    """Detect exact/format-only overlap. Semantic independence still requires human review."""
    raw = task if isinstance(task, str) else task.user_task
    text = unicodedata.normalize("NFKC", raw).casefold()
    text = "".join(
        c for c in text if not c.isspace() and not unicodedata.category(c).startswith("P")
    )
    return fingerprint(text)


def require_review(suite):
    from agenttest.eval_v2.models import Suite

    examples = Path(__file__).resolve().parents[3] / "examples"
    development = Suite.load(examples / "eval-v2/c-drafts.json")
    used = {task_identity(c.task) for c in development.cases}
    for filename in ("nl-tasks.json", "stage4b-tasks.json"):
        with (examples / filename).open("rb") as stream:
            raw = stream.read(1_000_001)
        legacy = bounded_json(raw.decode("utf-8"), 1_000_000)
        used.update(task_identity(c["user_task"]) for c in legacy["cases"])
    identities = [task_identity(c.task) for c in suite.cases]
    if len(set(identities)) != len(identities) or used.intersection(identities):
        raise ValueError("Development/holdout task overlap, independent of case ID")
    for case in suite.cases:
        r = case.review_record
        if (
            r is None or r.participated_in_development or not r.operator_independence_confirmed
            or r.task_sha256 != fingerprint(case.task.model_dump())
            or r.labels_sha256 != fingerprint(case.labels.model_dump())
            or r.decision != case.labels.expected_behavior
            or r.fixture_version != case.task.fixture_version
            or r.decision == "review"
            or datetime.fromisoformat(r.reviewed_at.replace("Z", "+00:00"))
            < datetime.fromisoformat(r.sampled_at.replace("Z", "+00:00"))
            or (r.decision == "reject" and not any(x.strip() for x in r.rejection_evidence))
        ):
            raise ValueError("Complete, consistent independent human review record required")
    # Bind human decisions to the exact trusted fixture, not a freely named contract.
    from agenttest.eval_v2.runner import EvalConfig, prepare_inputs

    inputs = prepare_inputs(suite, EvalConfig(development=True))[2]
    for case in suite.cases:
        if any(
            getattr(case.review_record, key) != inputs[key]
            for key in ("contract_sha256", "policy_sha256", "target_config_sha256")
        ):
            raise ValueError("Review record trusted input mismatch")
    # Static safety validation only. Human intent labels remain the independent quality rubric.
    import json

    from agenttest.contracts import load_openapi
    from agenttest.eval_v2.runner import oracle_plan
    from agenttest.executor import Executor
    from agenttest.intent import validate_intent
    from agenttest.natural_demo import natural_inputs

    target, target_config, registry, environ = natural_inputs()
    validator = Executor(target_config, load_openapi(json.dumps(target.openapi()).encode()),
                         environ=environ, registry=registry)
    for case in suite.cases:
        if case.labels.expected_behavior == "execute":
            plan = oracle_plan(case)
            validator.validate(plan)
            validate_intent(case.task.user_task, plan)


def review_payload(suite):
    return {
        "schema_version": "agenttest.eval.review.v1",
        "dataset_version": suite.dataset_version,
        "suite_sha256": fingerprint(suite.model_dump()),
        "case_tasks": {c.case_id: task_identity(c.task) for c in suite.cases},
        "review_records_sha256": fingerprint([c.review_record.model_dump() for c in suite.cases]),
        "identity_limit": "Record consistency only; operator verifies identity and independence.",
    }


def freeze_review(suite, registry):
    """One exclusive file per version in the operator-owned review registry."""
    suite.require_formal()
    payload = review_payload(suite)
    path = registry / (suite.dataset_version + ".json")
    save_evaluation(payload, path)
    return {"path": str(path), "sha256": fingerprint(payload), **payload}


def verify_review_lock(suite, path):
    suite.require_formal()
    if path is None:
        raise ValueError("Formal evaluation requires an independently frozen review lock")
    with path.open("rb") as stream:
        raw = stream.read(1_000_001)
    payload = bounded_json(raw.decode("utf-8"), 1_000_000)
    if payload != review_payload(suite):
        raise ValueError("Review lock changed: tasks/labels require a new reviewed version")
    return fingerprint(payload)


def review_template():
    """Blank enrollment, not another dataset or a fabricated human approval."""
    return {
        "schema_version": "agenttest.eval.review.enrollment.v1",
        "status": "UNREVIEWED",
        "suggested_first_batch": {"supported": "4-6", "safe_rejection": "2-3"},
        "instructions": "Operator supplies new raw tasks and evidence; never promote C drafts.",
        "task": {
            "user_task": "", "target_profile_id": "mini_nl_v1",
            "contract_ref": "mini_target.openapi", "fixture_version": "mini-nl-v1",
            "policy_registry_ref": "examples/nl-policies.json",
        },
        "labels": {
            "support_status": "review", "expected_behavior": "review", "rubric": None,
            "rejection_basis": [], "faults": [], "limitation": "Pending operator labels",
            "review_status": "UNREVIEWED", "review_source": None,
            "label_provenance": "ai_generated_draft",
        },
        "review_record": {
            name: False if name in {
                "participated_in_development", "operator_independence_confirmed"
            }
            else [] if name == "rejection_evidence" else None
            for name in ReviewRecord.model_fields
        },
        "rubric_fields": {
            "steps": ["key", "operation_id", "role", "policy_id", "arguments", "goals"],
            "argument": ["location", "name", "value OR source+selector"],
            "before": "Only required partial-order edges; independent steps may permute",
            "required_goals": "All independently reviewed subgoals",
            "extra_steps": "Forbidden by exact signature/argument set",
        },
    }
