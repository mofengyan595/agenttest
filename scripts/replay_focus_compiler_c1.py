"""Read three local acceptance archives; compile and validate without a model or HTTP."""

import argparse
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import httpx
from run_development_agent import ADAPTIVE_INPUT_TOKENS, limited_project

from agenttest.contracts import load_openapi
from agenttest.diagnostics import ValidationFailure
from agenttest.execution_budget import ADAPTIVE, ExecutionBudget
from agenttest.focus_compiler import FocusAvailability, compile_requested_checks
from agenttest.generic_intake import GenericSetup, build_generic_project
from agenttest.llm_planner import ModelSettings
from agenttest.product_checks import (
    RequestedCheck,
    check_index,
    partition_requested_checks,
    validate_requested_checks,
)
from agenttest.product_design import design_groups
from agenttest.product_focus import FocusSelection, TestFocusModel
from agenttest.product_focus_wire import operation_checks
from agenttest.product_obligations import approved_project_scope, negative_batches
from agenttest.product_sources import decode_source_refs, input_sources
from agenttest.product_tasks import request_planning_ir
from agenttest.report import fingerprint

ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS = ROOT / "tests/fixtures/focus-compiler-history.json"


def load(path):
    return json.loads(path.read_bytes())


def one_row(path, query, params):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1",
                                 uri=True)) as db:
        row = db.execute(query, params).fetchone()
    if row is None:
        raise ValueError("Missing frozen project or task")
    return row


def frozen_project(directory, task_id):
    workspace = directory / "lab/workspace"
    key, snapshot = one_row(workspace / "tasks.sqlite3",
        "SELECT project, identity FROM tasks WHERE id=?", (task_id,))
    raw, = one_row(workspace / "project-intake.sqlite3",
        "SELECT payload FROM projects WHERE id=?", (key,))
    record = json.loads(raw)
    setup = GenericSetup.model_validate(record["generic"])
    assert not setup.roles and not any(op.credential_inputs for op in setup.operations)
    document = record["document"]
    project = build_generic_project(key, document, load_openapi(document), setup, (), {})
    # Match the historical worker's runtime snapshot without loading credentials or a service.
    revision = fingerprint(dict(record=record, enabled=True, ips=(), credentials={}))
    project = replace(project, document=document.encode(), intake_revision=revision)
    project = limited_project(project, max_requests=120, max_model_calls=64,
        max_input=ADAPTIVE_INPUT_TOKENS, max_output=8192, max_seconds=900,
        supervisor_rounds=0, loop_engine="DEEP_AGENTS", agent_max_turns=40,
        agent_max_tool_calls=60, agent_max_model_calls=0, execution=ExecutionBudget(
            mode=ADAPTIVE, model_calls=16, total_tokens=180000, model_calls_ceiling=64,
            total_tokens_ceiling=650000, agent_turns_ceiling=40, agent_tool_calls_ceiling=60,
            seconds_ceiling=900, no_progress_limit=3))
    assert project.identity() == snapshot, f"Project snapshot mismatch in {directory.name}"
    return project


def individually_valid_refs(scope, context):
    """Check each submitted row for comparison, independent of whole-proposal acceptance."""
    catalogue = operation_checks(context["approved_checks"])
    sources = input_sources(context)
    valid = set()
    for operation, bundle in scope["operations"].items():
        if not isinstance(bundle, dict):
            continue
        for row in bundle["requested_checks"]:
            entry = catalogue.get(operation, {}).get(row["check_ref"])
            if entry is None:
                continue
            candidate = {key: value for key, value in row.items() if key != "check_ref"}
            candidate["policy_id"] = entry["policy_id"]
            try:
                decode_source_refs({"requested_checks": [candidate]}, sources, context)
                check = RequestedCheck.model_validate(candidate)
                validate_requested_checks([check], context, {operation})
            except ValueError:
                continue
            valid.add((operation, row["check_ref"], row["source_ref"]))
    return sorted(valid)


def check_attempt(raw, event, project, focus, expectations):
    context = event["context"]
    assert context["approved_checks"] == check_index(project)
    ready = frozenset(context["focus_ready_operations"])
    identities = frozenset(row["identity"] for row in context["managed_authentication"]
                           if row["credential_status"] == "READY")
    pending = frozenset((row["operation_id"], row["role"]) for row in context["pending_checks"])
    compiled = compile_requested_checks(project,
        FocusAvailability(project.identity(), ready, identities, pending),
        text=context["user_task"],
        prior_scope=bool(context.get("previous_requests") or context.get("previous_result")))
    assert compiled.status == "COMPILED" and not compiled.pending
    expected = {(op, key, f"check_{i}", expectations["source_ref"], (expectations["role"],))
                for op, keys in expectations["requested_checks"].items()
                for i, key in enumerate(keys, 1)}
    actual = {(row.operation_id, row.policy_id, row.check_ref, row.source.source_ref, row.roles)
              for row in compiled.mappings}
    assert actual == expected and len(actual) == len(compiled.mappings) == len(compiled.executable)
    assert all(not row.variant.model_dump() and row.source.clause_id in
               context["trusted_scope_clauses"] for row in compiled.mappings)

    wire = json.loads(raw)
    scope = wire["scope"]
    submitted = [entry for bundle in scope["operations"].values() if isinstance(bundle, dict)
                 for entry in bundle["requested_checks"]]
    verdict, selection = "SELECTED", None
    try:
        selection = FocusSelection.model_validate_json(focus._decode_output(raw, context))
    except ValidationFailure as error:
        verdict = error.diagnostic.code
    archived = event.get("output")
    assert (selection is None) == (archived is None)
    if isinstance(archived, str):
        archived = json.loads(archived)
    compiled_ids = {check.policy_id for check in compiled.executable}
    accepted_count = None
    if selection is not None:
        actual_checks = [check.model_dump() for check in selection.requested_checks]
        assert {check.policy_id for check in selection.requested_checks} == compiled_ids
        assert [check.model_dump() for check in selection.requested_checks] == [
            RequestedCheck.model_validate(check).model_dump()
            for check in archived["requested_checks"]]
        executable, pending_values = partition_requested_checks(project, actual_checks,
            input_requirements=[item.model_dump() for item in selection.input_requirements],
            input_questions=[item.model_dump() for item in selection.input_questions])
        assert not pending_values and {row["policy_id"] for row in executable} == compiled_ids
        # Scope and case obligations remain separately owned by the original Planning IR.
        atoms, text = context["request_atoms"], context["user_task"]
        ir = request_planning_ir(project, selection, atoms, text, autonomous=True)
        shadow_selection = selection.model_copy(update={"requested_checks": compiled.executable})
        assert request_planning_ir(project, shadow_selection, atoms, text, autonomous=True) == ir
        accepted_count = len(executable)
    groups = design_groups(project, operation_ids=context["approved_ids"])
    batches = negative_batches(project, {"groups": groups})
    ownership = {key: group["id"] for group in batches for key in group["policy_ids"]}
    assert set(ownership) == compiled_ids
    extras = set(wire["requirement_map"]) & set(context["trusted_scope_clauses"])
    return dict(verdict=verdict, mode=scope["mode"], submitted=len(submitted),
        individually_valid_refs=individually_valid_refs(scope, context),
        submitted_fields=sum(len(row) for row in submitted),
        explicit_overrides=sum(any(c.get(k) is not None for k in ("exact_value", "exact_length"))
                               for c in submitted),
        extra_scope_clause=bool(extras),
        shadow=len(compiled_ids), shadow_pending=0, original_executable=accepted_count,
        ir_compared=selection is not None, batches=len(batches), ownership=ownership)


def replay(archive_root):
    expectations = load(EXPECTATIONS)

    def forbid_request(_):
        raise AssertionError("Replay must not open a model transport")

    focus = TestFocusModel(ModelSettings(model="offline", base_url="https://offline.invalid",
                                        api_key="offline-replay-placeholder"), profile="generic",
                           transport=httpx.MockTransport(forbid_request))
    receipts = []
    for batch, expected in expectations["archives"].items():
        archive = archive_root / batch
        score = load(archive / "audit.json")
        assert score["qualifies"] == expected["qualifies"] and score["decision"] == "NO-GO"
        for position, verdicts in enumerate(expected["rounds"], 1):
            directory = archive / f"release-v{position}"
            phase = load(sorted(directory.glob("phase-*.json"))[-1])
            project = frozen_project(directory, phase["task_id"])
            assert len(approved_project_scope(project)) == expectations["approved_denominator"]
            trace = directory / "model-outputs.jsonl"
            attempts = []
            raw = None
            for line in trace.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("model") != "focus":
                    continue
                if event.get("stage") == "decode":
                    raw = event["raw_output"]
                elif event.get("context"):
                    assert raw is not None
                    attempts.append(check_attempt(raw, event, project, focus, expectations))
                    raw = None
            assert [item["verdict"] for item in attempts] == verdicts, (batch, position, attempts)
            summary = phase["summary"]
            records = [r for r in summary["design_records"] if r["group_id"] == "focus"]
            assert [(r.get("diagnostic") or {}).get("code") or r["status"]
                    for r in records] == verdicts
            historical = {key: group["id"] for group in summary["groups"]
                          for key in group.get("policy_ids", ())}
            if verdicts[-1] == "SELECTED":
                assert historical == attempts[-1]["ownership"]
                assert summary["coverage"]["requested_check_denominator"] == attempts[-1]["shadow"]
            receipts.append(dict(batch=batch, round=position, trace_sha256=sha256(
                trace.read_bytes()).hexdigest(), qualifies=score["qualifies"],
                original_request_denominator=summary["coverage"]["requested_check_denominator"],
                approved_denominator=expectations["approved_denominator"], attempts=attempts))
    return receipts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archives", type=Path, default=ROOT / ".eduwork")
    args = parser.parse_args()
    print(json.dumps(replay(args.archives), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
