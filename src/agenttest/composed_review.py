"""Independent enrollment for composition planning; never certify reviewer identity."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from agenttest.composed_demo import composed_inputs
from agenttest.composed_evaluation import CompositionCase, CompositionSuite, reference_draft
from agenttest.composed_intent import interpret_composed_task, validate_composed_intent
from agenttest.contracts import summarize_openapi
from agenttest.demo import PROJECT_ROOT
from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.review import ReviewRecord, review_template, task_identity
from agenttest.evaluation import save_evaluation
from agenttest.executor import Executor
from agenttest.planner_input import validate_user_task
from agenttest.policies import NeedsReview
from agenttest.report import fingerprint
from agenttest.safety import StrictModel

FIXTURE_VERSION = "mini-composed-v1"


class PlanningCase(CompositionCase):
    review_status: Literal["UNREVIEWED", "HUMAN_REVIEWED"] = "UNREVIEWED"
    label_provenance: Literal["ai_generated_draft", "developer", "independent_human"]
    review_record: ReviewRecord | None = None

    @model_validator(mode="after")
    def reviewed_basis(self):
        validate_user_task(self.user_task)
        if self.review_status == "HUMAN_REVIEWED" and (
            self.label_provenance != "independent_human" or self.review_record is None
        ):
            raise ValueError("Independent records required for human-reviewed labels")
        return self


class PlanningSuite(StrictModel):
    schema_version: Literal["agenttest.composition.planning.v1"] = (
        "agenttest.composition.planning.v1"
    )
    dataset_version: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    split: Literal["development", "independent_holdout"] = "development"
    cases: list[PlanningCase] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate planning case identity")
        if self.split == "development" and any(
            c.review_status != "UNREVIEWED" or c.review_record for c in self.cases
        ):
            raise ValueError("Development fixtures cannot claim human review")
        return self

    @classmethod
    def load(cls, path: Path):
        data = read_json(path)
        if data.get("schema_version") == "agenttest.composition.development.v1":
            old = CompositionSuite.model_validate(data)
            data = {
                "dataset_version": "composed-development-v1",
                "split": "development",
                "cases": [
                    {**c.model_dump(), "label_provenance": old.label_provenance} for c in old.cases
                ],
            }
        return cls.model_validate(data)


def label_payload(case: PlanningCase) -> dict:
    return {
        "expected": case.expected.model_dump() if case.expected else None,
        "rejection_code": case.rejection_code,
    }


def trusted_composition():
    target, config, registry, environ = composed_inputs()
    contract = summarize_openapi(target.openapi())
    return (
        contract,
        config,
        registry,
        environ,
        {
            "contract_sha256": fingerprint(contract.model_dump()),
            "policy_sha256": registry.digest(),
            "target_config_sha256": fingerprint(config.model_dump()),
            "fixture_version": FIXTURE_VERSION,
        },
    )


def used_task_identities() -> set[str]:
    """Exact/format overlap only; independent sampling still requires a human operator."""
    tasks = [
        c.user_task
        for path in sorted((PROJECT_ROOT / "examples").glob("composed*-development.json"))
        for c in PlanningSuite.load(path).cases
    ]
    for name in ("nl-tasks.json", "stage4b-tasks.json"):
        tasks.extend(c["user_task"] for c in read_json(PROJECT_ROOT / "examples" / name)["cases"])
    tasks.extend(
        c["task"]["user_task"]
        for c in read_json(PROJECT_ROOT / "examples/eval-v2/c-drafts.json")["cases"]
    )
    return {task_identity(task) for task in tasks}


def require_planning_review(suite: PlanningSuite) -> dict:
    """Reuse review-record types, not the formal Eval v2 schema or its scoring rules."""
    if suite.split != "independent_holdout" or any(
        c.review_status != "HUMAN_REVIEWED" or c.label_provenance != "independent_human"
        for c in suite.cases
    ):
        raise ValueError("Independently reviewed new tasks required")
    identities = [task_identity(c.user_task) for c in suite.cases]
    if len(set(identities)) != len(identities) or used_task_identities().intersection(identities):
        raise ValueError("Used/development task overlap")
    contract, config, registry, environ, inputs = trusted_composition()
    validator = Executor(config, contract, environ=environ, registry=registry)
    for case in suite.cases:
        record = case.review_record
        if (
            record is None
            or record.participated_in_development
            or not record.operator_independence_confirmed
            or record.task_sha256 != fingerprint(case.user_task)
            or record.labels_sha256 != fingerprint(label_payload(case))
            or record.decision != ("execute" if case.expected else "reject")
            or any(getattr(record, key) != value for key, value in inputs.items())
            or datetime.fromisoformat(record.reviewed_at.replace("Z", "+00:00"))
            < datetime.fromisoformat(record.sampled_at.replace("Z", "+00:00"))
            or not case.expected
            and not any(x.strip() for x in record.rejection_evidence)
        ):
            raise ValueError("Incomplete or inconsistent independent composition review")
        if case.expected:
            plan = reference_draft(case.expected).scenario()
            validator.validate(plan)
            validate_composed_intent(case.user_task, plan)
        else:
            try:
                interpret_composed_task(case.user_task)
            except NeedsReview as exc:
                if exc.diagnostic.code != case.rejection_code:
                    raise ValueError("Review rejection basis mismatch") from None
            else:
                raise ValueError("Supported task cannot be labeled as a safe rejection")
    return {
        "schema_version": "agenttest.composition.planning.review.v1",
        "dataset_version": suite.dataset_version,
        "suite_sha256": fingerprint(suite.model_dump()),
        "inputs": inputs,
        "review_records_sha256": fingerprint([c.review_record.model_dump() for c in suite.cases]),
        "identity_limit": "Record consistency only; operator verifies identity and independence.",
    }


def freeze_planning_review(suite: PlanningSuite, registry: Path) -> dict:
    payload = require_planning_review(suite)
    save_evaluation(payload, registry / (suite.dataset_version + ".json"))
    return payload


def verify_planning_review(suite: PlanningSuite, path: Path | None) -> str:
    if (
        path is None
        or path.name != suite.dataset_version + ".json"
        or read_json(path) != require_planning_review(suite)
    ):
        raise ValueError("An unchanged independent review lock is required")
    return fingerprint(read_json(path))


def planning_review_packet(suite: PlanningSuite) -> dict:
    """Pre-fill digests for an operator's tasks, never identity, independence or approval."""
    result = planning_review_template()
    result["dataset_version"] = suite.dataset_version
    result["split"] = suite.split
    result["cases"] = []
    for case in suite.cases:
        record = {
            **review_template()["review_record"],
            **result["trusted_inputs"],
            "task_sha256": fingerprint(case.user_task),
            "labels_sha256": fingerprint(label_payload(case)),
            "decision": "execute" if case.expected else "reject",
        }
        result["cases"].append(
            {**case.model_dump(), "review_status": "UNREVIEWED", "review_record": record}
        )
    result["contains_used_tasks"] = any(
        task_identity(c.user_task) in used_task_identities() for c in suite.cases
    )
    return result


def planning_review_template() -> dict:
    inputs = trusted_composition()[-1]
    return {
        "schema_version": "agenttest.composition.planning.enrollment.v1",
        "status": "UNREVIEWED",
        "ready_for_paid_run": False,
        "model_calls": 0,
        "instructions": "Supply new independent raw tasks and labels; never promote used P/C/T/B.",
        "suggested_batch": {"supported": 5, "rejection": 3},
        "trusted_inputs": inputs,
        "case": {
            "case_id": "P101",
            "user_task": "",
            "review_status": "UNREVIEWED",
            "label_provenance": None,
            "expected": None,
            "rejection_code": None,
            "review_record": review_template()["review_record"],
        },
        "label_fields": {
            "creator": "alice or bob",
            "course_name": "exact quoted name",
            "goals": ["consistency", "ownership", "batch"],
            "rejection": "one approved INTENT_COMPOSED rejection code",
        },
        "limits": [
            "Batch remains alice-only",
            "Ownership requires owner consistency",
            "Controlled clauses only",
            "Record source, sampling and independent reviewer",
        ],
        "used_task_identity_sha256": sorted(used_task_identities()),
    }
