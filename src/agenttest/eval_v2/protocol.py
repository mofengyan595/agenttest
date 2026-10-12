"""Prepare a paired experiment without reading credentials or making model requests."""

from urllib.parse import urlsplit

from agenttest.eval_v2.freeze import checkout_identity
from agenttest.eval_v2.review import verify_review_lock
from agenttest.eval_v2.runner import EvalConfig, prepare_inputs
from agenttest.evaluation_freeze import public_model_config
from agenttest.llm_planner import ModelSettings
from agenttest.report import fingerprint


def prepare_protocol(suite, root, *, review_lock=None, model=None, endpoint=None,
                     max_calls=0, max_total_tokens=0, max_input_tokens=20000,
                     max_output_tokens=2048, timeout=30.0, max_http_requests=40,
                     max_workflow_seconds=90.0, candidate_max_bytes=6000):
    blockers, lock_digest = ["EXPLICIT_OPERATOR_AUTHORIZATION_REQUIRED"], None
    try:
        lock_digest = verify_review_lock(suite, review_lock)
    except ValueError:
        blockers.append("INDEPENDENT_REVIEWED_HOLDOUT_AND_LOCK_REQUIRED")
    if not model or not endpoint or max_calls < 1 or max_total_tokens < 1:
        blockers.append("EXACT_MODEL_ENDPOINT_AND_BUDGET_REQUIRED")
    settings = ModelSettings(
        model=model or "pending", base_url=endpoint or "https://protocol.invalid",
        api_key="protocol-placeholder",
        protocol="chatecnu" if urlsplit(endpoint or "").hostname == "chat.ecnu.edu.cn"
        else "openai_compatible",
        max_input_tokens=max_input_tokens, max_output_tokens=max_output_tokens,
        max_total_tokens=max_input_tokens + max_output_tokens, timeout=timeout,
    )
    if not 0 <= max_calls <= 200 or max_total_tokens < 0:
        raise ValueError("Invalid experiment budget")
    config = EvalConfig(
        development=True, variant="B", max_http_requests=max_http_requests,
        max_workflow_seconds=max_workflow_seconds, candidate_max_bytes=candidate_max_bytes,
    )
    a, b, inputs, _ = prepare_inputs(suite, config)
    checkout = checkout_identity(root)
    if checkout["worktree_status"]:
        blockers.append("CLEAN_COMMITTED_CHECKOUT_AND_NEW_SHA_CI_REQUIRED")
    reservation = max_input_tokens + max_output_tokens
    scheduled = sum(c.labels.expected_behavior != "review" for c in suite.cases)
    if model and endpoint and (max_calls < scheduled or max_total_tokens < scheduled * reservation):
        blockers.append("BUDGET_CANNOT_COVER_EVERY_SCHEDULED_CASE")
    execution_workflows = sum(c.labels.expected_behavior == "execute" for c in suite.cases)
    execution_workflows += 2 * sum(len(c.labels.faults) for c in suite.cases)
    common = [
        "python", "scripts/evaluate_v2.py", "run", "--source", "real", "--real",
        "--suite", "<operator-reviewed-suite.json>", "--review-lock", "<review-version-lock.json>",
        "--model-env", "<operator-private-env>", "--allow-model", model or "<approved-model>",
        "--allow-endpoint", endpoint or "<approved-https-endpoint>",
        "--max-calls", str(max_calls), "--max-total-tokens", str(max_total_tokens),
        "--max-input-tokens", str(max_input_tokens), "--max-output-tokens", str(max_output_tokens),
        "--timeout", str(timeout), "--max-http-requests", str(max_http_requests),
        "--max-workflow-seconds", str(max_workflow_seconds),
        "--candidate-max-bytes", str(candidate_max_bytes),
    ]
    return {
        "schema_version": "agenttest.eval.protocol.v1", "ready_to_run": False,
        "operator_authorized": False, "blockers": blockers,
        "model_calls": 0, "business_http_requests": 0, "observed_tokens": None,
        "git": checkout, "suite_sha256": fingerprint(suite.model_dump()),
        "review_lock_sha256": lock_digest, "trusted_inputs": inputs,
        "model": model, "endpoint_sha256": fingerprint(endpoint.rstrip("/")) if endpoint else None,
        "model_config": public_model_config(settings) if model and endpoint else None,
        "generation": {"retries": 0, "response_format": "json_schema",
                       "temperature": 0 if settings.protocol == "chatecnu" else None,
                       "thinking": "disabled" if settings.protocol == "chatecnu" else None},
        "per_group": {"max_calls": max_calls, "max_total_tokens": max_total_tokens,
                      "per_call_reserved_tokens": reservation, "scheduled_calls": scheduled,
                      "http_requests_per_workflow": max_http_requests,
                      "workflow_seconds": max_workflow_seconds},
        "business_http_ceiling_per_group": execution_workflows * max_http_requests,
        "combined_authorization_ceiling": {
            "model_calls": 2 * max_calls, "total_tokens": 2 * max_total_tokens,
        },
        "contexts_sha256": {"A": fingerprint(a), "B": fingerprint(b)},
        "command_templates": {
            v: common + ["--variant", v, "--output", "runs/<unique-experiment-id>/" + v]
            for v in ("A", "B")
        },
        "analysis": [
            "Paired plan validity, independent intent and end-to-end transitions",
            "Separate planner rejection, safety-gate rejection, errors and unattempted cases",
            "Separate observed input/output/total tokens and latency for each variant",
            "No statistical significance claim from a small sample or Fake/Mock results",
        ],
        "stop_rules": ["unknown usage", "budget exhausted", "freeze drift", "no retries"],
    }
