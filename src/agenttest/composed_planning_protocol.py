"""Frozen model inputs and conservative budgets for a separate planning experiment."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field

from agenttest.composed_demo import composed_inputs
from agenttest.composed_plan import COMPOSED_PROMPT, ComposedPlannerDraft
from agenttest.composed_review import PlanningSuite, trusted_composition, verify_planning_review
from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.context import assert_no_labels
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import public_model_config
from agenttest.llm_planner import ModelSettings, OpenAICompatiblePlanner
from agenttest.planner_input import planning_context
from agenttest.report import fingerprint
from agenttest.safety import StrictModel


class PlanningConfig(StrictModel):
    source: Literal["fake", "mock_transport", "real"] = "mock_transport"
    real_opt_in: bool = False
    model: str = "ecnu-plus"
    endpoint: str = "https://chat.ecnu.edu.cn/open/api/v1"
    max_calls: int = Field(default=8, ge=0, le=16)
    max_total_tokens: int = Field(default=192_768, ge=0, le=385_536)
    max_input_tokens: int = Field(default=20_000, ge=1, le=20_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=4096)
    max_http_requests: int = Field(default=10, ge=1, le=10)
    model_timeout: float = Field(default=30, gt=0, le=60)
    workflow_timeout: float = Field(default=90, gt=0, le=120)

    @property
    def token_reservation(self) -> int:
        return self.max_input_tokens + self.max_output_tokens


def planning_settings(config: PlanningConfig, settings: ModelSettings | None = None):
    template = ModelSettings.from_environment(
        {
            "AGENTTEST_MODEL": config.model,
            "OPENAI_BASE_URL": config.endpoint,
            "OPENAI_API_KEY": "offline-planning-fixture-only",
        }
    )
    settings = template if settings is None else settings
    if (
        settings.model != config.model
        or settings.base_url != config.endpoint
        or settings.protocol != template.protocol
    ):
        raise ValueError("Approved model identity/endpoint mismatch")
    return ModelSettings.model_validate(
        {
            **settings.model_dump(),
            "timeout": config.model_timeout,
            "max_input_tokens": config.max_input_tokens,
            "max_output_tokens": config.max_output_tokens,
            "max_total_tokens": config.token_reservation,
        }
    )


def planning_inputs(suite: PlanningSuite, config: PlanningConfig):
    from agenttest.dsl import Scenario

    contract, target, registry, _, identities = trusted_composition()
    target.max_requests = min(target.max_requests, config.max_http_requests)
    contexts, requests = {}, {}
    adapter = OpenAICompatiblePlanner(planning_settings(config), profile="mini_composed")
    for case in suite.cases:
        context = planning_context(
            contract, target, registry, case.user_task, Scenario.model_json_schema()
        )
        assert_no_labels(context)
        if {"expected", "goals", "rejection_code", "review_record"} & context.keys():
            raise ValueError("Planning labels must remain outside model input")
        adapter.prepare_request(context)  # Encode/budget-check only, no HTTP.
        contexts[case.case_id] = context
        requests[case.case_id] = {
            "request_sha256": adapter.stats.request_sha256,
            "request_bytes": adapter.stats.request_bytes,
            "input_token_upper_bound": adapter.stats.input_token_upper_bound,
        }
    identities = {
        **identities,
        "raw_contract_sha256": fingerprint(
            json.dumps(composed_inputs()[0].openapi()).encode().hex()
        ),
        "execution_target_sha256": fingerprint(target.model_dump()),
        "contexts_sha256": fingerprint(contexts),
    }
    return contexts, requests, identities, registry


@dataclass
class PlanningLedger:
    """Reserve worst case before generation; never refund a slot or unknown usage."""

    slots: int = 0
    reserved_tokens: int = 0
    calls: int = 0
    real_calls: int = 0
    stop: str | None = None

    def reserve(self, config: PlanningConfig) -> bool:
        if (
            self.slots >= config.max_calls
            or self.reserved_tokens + config.token_reservation > config.max_total_tokens
        ):
            self.stop = "NOT_ATTEMPTED_BUDGET"
            return False
        self.slots += 1
        self.reserved_tokens += config.token_reservation
        return True


class PlanningFreeze:
    def __init__(
        self,
        root: Path,
        suite: PlanningSuite,
        config: PlanningConfig,
        directory: Path,
        *,
        suite_path: Path | None = None,
        responses_path: Path | None = None,
        review_lock: Path | None = None,
    ):
        self.root, self.suite, self.config, self.directory = root, suite, config, directory
        self.suite_path, self.responses_path, self.review_lock = (
            suite_path,
            responses_path,
            review_lock,
        )
        self.manifest = self.payload()

    def payload(self) -> dict:
        contexts, requests, inputs, registry = planning_inputs(self.suite, self.config)
        return {
            "schema_version": "agenttest.composition.planning.freeze.v1",
            "git": checkout_identity(self.root),
            "suite": self.suite.model_dump(mode="json"),
            "suite_sha256": fingerprint(self.suite.model_dump()),
            "suite_file_sha256": fingerprint(read_json(self.suite_path))
            if self.suite_path
            else None,
            "responses_sha256": fingerprint(read_json(self.responses_path))
            if self.responses_path
            else None,
            "review_lock_sha256": verify_planning_review(self.suite, self.review_lock)
            if self.config.source == "real"
            else None,
            "review_lock": read_json(self.review_lock) if self.config.source == "real" else None,
            "inputs": inputs,
            "requests": requests,
            "contexts_sha256": fingerprint(contexts),
            "registry": [p.model_dump(mode="json") for p in registry.definitions()],
            "config": self.config.model_dump(mode="json", exclude={"real_opt_in"}),
            "model_config": public_model_config(planning_settings(self.config)),
            "prompt_sha256": fingerprint(COMPOSED_PROMPT),
            "draft_schema_sha256": fingerprint(ComposedPlannerDraft.model_json_schema()),
            "formal_eval_v2_score": None,
            "retries": 0,
        }

    def verify(self) -> None:
        if (
            self.payload() != self.manifest
            or read_json(self.directory / "freeze.json") != self.manifest
        ):
            raise ValueError("PLANNING_FREEZE_CHANGED")


def prepare_planning(
    suite: PlanningSuite,
    config: PlanningConfig,
    output: Path,
    *,
    root: Path,
    review_lock: Path | None = None,
) -> dict:
    """No credentials, transports or authorization; prepare is never a paid-run approval."""
    contexts, requests, inputs, _ = planning_inputs(suite, config)
    git = checkout_identity(root)
    review_sha = verify_planning_review(suite, review_lock) if review_lock else None
    proposal = {
        "schema_version": "agenttest.composition.planning.proposal.v1",
        "operator_authorized": False,
        "ready_for_paid_run": False,
        "real_model_calls": 0,
        "suite_sha256": fingerprint(suite.model_dump()),
        "inputs": inputs,
        "requests": requests,
        "git": git,
        "config": config.model_dump(mode="json", exclude={"real_opt_in"}),
        "model_config": public_model_config(planning_settings(config)),
        "review_lock_sha256": review_sha,
        "ceiling": {
            "model_calls": config.max_calls,
            "reserved_tokens": config.max_total_tokens,
            "per_call_reserved_tokens": config.token_reservation,
            "business_http_requests": min(len(suite.cases), config.max_calls)
            * config.max_http_requests,
        },
        "remaining_conditions": [
            "Explicit operator authorization for model quota",
            *(
                ["Independent new tasks, labels and immutable review lock"]
                if not review_sha
                else []
            ),
            *(
                ["Clean committed implementation and regenerate proposal"]
                if git["worktree_status"]
                else []
            ),
        ],
        "formal_eval_v2_score": None,
    }
    output.mkdir(parents=True, exist_ok=False)
    save_evaluation(suite.model_dump(mode="json"), output / "suite.json")
    save_evaluation(contexts, output / "contexts.json")
    save_evaluation(proposal, output / "proposal.json")
    return proposal


def verify_live_proposal(path: Path | None, freeze: PlanningFreeze) -> None:
    if path is None:
        raise ValueError("An exact reviewed live-call proposal is required")
    proposal, frozen = read_json(path), freeze.manifest
    if (
        proposal.get("schema_version") != "agenttest.composition.planning.proposal.v1"
        or proposal.get("config") != frozen["config"]
        or any(
            proposal.get(key) != frozen[key]
            for key in (
                "git",
                "suite_sha256",
                "inputs",
                "requests",
                "model_config",
                "review_lock_sha256",
            )
        )
        or frozen["git"]["worktree_status"]
        or not frozen["review_lock_sha256"]
    ):
        raise ValueError("Live proposal differs from reviewed inputs, code or budgets")
