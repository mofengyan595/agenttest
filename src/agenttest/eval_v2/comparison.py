"""Paired A/B auditing. A development comparison never claims a model improvement."""

from agenttest.eval_v2.archive import verify_archive


def compare(a_directory, b_directory):
    (a, af), (b, bf) = [verify_archive(path) for path in (a_directory, b_directory)]
    if not a["freeze_valid"] or not b["freeze_valid"]:
        raise ValueError("Invalid freeze cannot support a comparison")
    if a["variant"] != "A" or b["variant"] != "B":
        raise ValueError("Expected distinct A and B runs")
    for key in ("schema_version", "source", "dataset_version", "review_status"):
        if a[key] != b[key]:
            raise ValueError("Unpaired evaluation identity")
    for key in (
        "evaluator_version",
        "git",
        "tasks_sha256",
        "labels_sha256",
        "suite_sha256",
        "prompt_sha256",
        "planner_schema_sha256",
        "model_config",
        "responses_sha256",
        "review_lock_sha256",
        "environment",
    ):
        if af[key] != bf[key]:
            raise ValueError("Unpaired frozen input: " + key)
    for key in ("contract_sha256", "policy_sha256", "target_config_sha256"):
        if af["inputs"][key] != bf["inputs"][key]:
            raise ValueError("Unpaired trusted permissions")
    ac, bc = [dict(f["evaluation_config"]) for f in (af, bf)]
    ac.pop("variant")
    bc.pop("variant")
    if ac != bc:
        raise ValueError("Unequal A/B budgets/configuration")
    if a["stop_reason"] or b["stop_reason"]:
        raise ValueError("Stopped run cannot support an A/B comparison")
    ar, br = [{r["case_id"]: r for r in run["results"]} for run in (a, b)]
    if ar.keys() != br.keys():
        raise ValueError("Unequal paired cases")
    fields = (
        "classification",
        "plan_valid",
        "intent",
        "safe_rejection",
        "rejection_kind",
        "end_to_end_success",
        "context_bytes",
        "model_calls",
        "real_api_calls",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "model_elapsed_ms",
        "workflow_elapsed_ms",
        "http_requests",
    )
    return {
        "source": a["source"],
        "dataset_version": a["dataset_version"],
        "formal_independent_evaluation": a["formal_independent_evaluation"],
        "statistical_significance": "NOT_ESTABLISHED",
        "conclusion_limit": "Paired observations only; Fake/Mock cannot show real-model gain.",
        "A": a["summary"],
        "B": b["summary"],
        "paired_results": [
            {
                "case_id": key,
                "A": {k: ar[key][k] for k in fields},
                "B": {k: br[key][k] for k in fields},
            }
            for key in ar
        ],
        "classification_transitions": [
            {"case_id": key, "from": ar[key]["classification"], "to": br[key]["classification"]}
            for key in ar
        ],
    }
