"""One frozen, socket-free comparison of approved contract-data construction."""

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import socket
import sqlite3
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from compare_contract_tools import data_evidence
from jsonschema import Draft202012Validator
from pydantic import SecretStr

from agenttest.contracts import load_openapi
from agenttest.dsl import ArgumentBinding, Scenario
from agenttest.executor import Executor
from agenttest.goal_suite import BudgetTransport, SuiteLedger
from agenttest.human_review import HumanReviews, ReviewDecision, group_operations
from agenttest.llm_planner import ModelSettings
from agenttest.negative import mutate_request, select_negative_approval
from agenttest.plan_construction import construct_plan
from agenttest.planning_compiler import compile_design
from agenttest.planning_ir import ActionInstance, InstanceArgument, PlanningIR
from agenttest.policies import PolicyRegistry
from agenttest.product_design import (
    DesignCase,
    TestDesignModel,
    design_context,
    propose_design,
    validate_design_case,
)
from agenttest.product_schema import encoded_literal
from agenttest.project_goals import GoalCatalog, GoalProject, RegisteredProject
from agenttest.report import ReportStore, RunReport, fingerprint
from agenttest.request_validation import request_schemas, validate_request, validate_value
from agenttest.safety import TargetConfig
from agenttest.step_recovery import StepJournal, StepRecoveryBlocked, StepScope, tool_attempts
from agenttest.task_service import ProgressStore
from agenttest.task_store import TaskStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/contract-tool-reuse-poc"
PARCEL = ROOT / ".eduwork/c1.2-focus-pairs/frozen/parcel.project.json"
ERRATUM_SHA256 = "f493fad404ddcd7fa90930005ced58e716eb8f65627a8e56e06de48d6e08becf"
# Literal test-only identities. The recording transport emits aliases and never saves headers.
IDENTITIES = {
    "POC_ASSET_EDITOR": "fixture-editor-value", "POC_ASSET_VIEWER": "fixture-viewer-value",
}
ARMS = ("A0", "C0", "B1")
OWNER = "poc"


def load(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()


def same_wire_value(left, right):
    try:
        return encoded(left) == encoded(right)
    except (TypeError, ValueError):
        return False


def checked_inputs():
    for name, digest in load(FIXTURES / "freeze.json").items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Frozen file changed: {name}")
    plan = load(FIXTURES / "positions.json")
    versions = {name: importlib.metadata.version(name) for name in plan["versions"]}
    if versions != plan["versions"] or list(sys.version_info[:3]) != plan["python"]:
        raise RuntimeError("Comparison interpreter or dependency version changed")
    if (plan["network_budget"] or plan["model_budget"]
            or sum(plan["count_by_mode"].values()) != plan["max_candidate_count"]):
        raise RuntimeError("Unexpected frozen comparison limits")
    return plan


def load_oracle():
    oracle = load(FIXTURES / "oracle.json")
    path = FIXTURES / "pre-run-erratum.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != ERRATUM_SHA256:
        raise RuntimeError("Pre-run response typing correction changed")
    item = load(path)
    body = oracle["approved"][item["position"]]["calls"][item["response_index"]]["response"]
    if body[item["field"]] != item["original"]:
        raise RuntimeError("Pre-run correction no longer matches frozen response")
    body[item["field"]] = item["corrected"]
    return oracle


def project(path):
    data = load(path)
    document = data["document"]
    raw = document.encode() if isinstance(document, str) else encoded(document)
    target = TargetConfig.model_validate(data["target"])
    if not httpx.URL(target.base_url).host == "127.0.0.1":
        raise RuntimeError("PoC target must use the frozen loopback origin")
    return GoalProject(RegisteredProject.model_validate(data["registration"]), raw,
                       load_openapi(raw), target,
                       PolicyRegistry.from_json(encoded(data["policies"])),
                       GoalCatalog.model_validate(data.get("catalog", {
                           "resource_aliases": ["asset"], "goals": [dict(id="list_assets",
                               aliases=["list assets"], policy_id="asset_list", role="viewer")]})))


@contextmanager
def no_network(attempts, phase):
    """Block socket connections and DNS after asyncio has initialized its local wakeup."""
    def forbidden(*_args, **_kwargs):
        attempts.append(phase)
        raise RuntimeError("POC_NETWORK_BLOCKED")

    with patch.object(socket.socket, "connect", forbidden), \
            patch.object(socket.socket, "connect_ex", forbidden), \
            patch.object(socket.socket, "sendto", forbidden), \
            patch.object(socket, "getaddrinfo", forbidden):
        yield


def diagnostic(exc):
    return exc.diagnostic.code if getattr(exc, "diagnostic", None) else str(exc)


def operation(proj, policy):
    return next(op for op in proj.contract.operations if op.operation_id == policy.operation_id)


def policies(proj):
    return {item.id: item for item in proj.registry.definitions()}


def context(proj, policy):
    group = dict(id="approved-check", operations=proj.target.allowed_operations,
                 primary=[policy.operation_id], policy_ids=[policy.id])
    return group, design_context(proj, "Run one approved input check", group, 12,
                                 len(proj.target.allowed_operations))


def draft_node(policy, step_id, literals, bindings=()):
    return dict(id=step_id, operation_id=policy.operation_id, role=policy.roles[0],
                policy_id=policy.id, arguments=[
                    dict(location=location, name=name, value=encoded_literal(value),
                         from_step=None, select=None)
                    for location, slots in literals.items() for name, value in slots.items()
                ] + list(bindings))


def trusted_nodes(subject, catalog, inputs):
    """Only resolve the preselected unique setup, never choose between producers."""
    check = catalog[subject["id"]]
    setup = subject.get("setup", {})
    before = [draft_node(catalog[pid], "prepare", values) for pid, values in setup.items()]
    bound = [dict(location=rule.location, name=rule.name, value=None, from_step="prepare",
                  select=rule.source_selector)
             for rule in check.binding_rules if rule.source_policy_id in setup]
    return before, draft_node(check, "negative", inputs, bound)


class FrozenDesigner(TestDesignModel):
    """A0 exercises production wire encoding/decoding with an offline literal proposal."""

    def __init__(self, wire):
        super().__init__(ModelSettings(model="poc-fixture", base_url="https://127.0.0.1",
                         api_key=SecretStr("poc-no-model-key"), max_input_tokens=180_000,
                         max_total_tokens=200_000), profile="generic")
        self.wire = wire
        self.request_sizes = []

    async def generate(self, public):
        request = self.prepare_request(public)
        self.request_sizes.append(len(request))
        schema = json.loads(request)["response_format"]["json_schema"]["schema"]
        Draft202012Validator(schema).validate(self.wire)
        return self._decode_output(encoded(self.wire).decode(), public)


def leaf_count(obj):
    if isinstance(obj, dict):
        return sum(leaf_count(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(leaf_count(v) for v in obj)
    return 1


async def a0(proj, subject, policy, group):
    # Baseline values are the independent frozen replacement model output.
    before, check = trusted_nodes(subject, policies(proj), subject["proposal"])
    reply = dict(interpretation="Run approved input check", relevant=True, cases=[],
                 questions=[], not_covered=[], required_cases={policy.id: dict(
                     title="Approved negative", before=before, check=check, after=[])})
    model = FrozenDesigner(reply)
    design = await propose_design(proj, "Run one approved input check", group, model,
        environ=IDENTITIES, remaining_requests=12, inventory_total=len(group["operations"]),
        requested_checks=[dict(policy_id=policy.id, source_quote="Run one approved input check")],
        available_identity_aliases=tuple(proj.target.roles), allow_optional_negatives=False,
        must_be_relevant=True)
    if len(design.cases) != 1:
        raise RuntimeError("A0 did not realize exactly one case")
    return design.cases[0], dict(design_calls=len(model.request_sizes),
        request_bytes=model.request_sizes, model_fields=leaf_count(reply),
        request_model_arguments=sum(len(s["arguments"]) for s in [*before, check]),
        response_bytes=len(encoded(reply)), compiled_by="TestDesignModel/construct_design")


def compile_case(before, check, public):
    plan, _ = construct_plan(dict(name="requested_check", steps=[*before, check]),
                             {**public, "composed_negative_chain": [
                                 s["id"] for s in [*before, check]]})
    return DesignCase.model_validate(dict(title="Approved negative", kind="negative", plan=plan))


def preflight(proj, case, store, *, cleanup=True):
    return validate_design_case(proj, case, store, environ=IDENTITIES,
                                verify_batch_cleanup=cleanup, observation_namespace=("poc", 0))


def raw_candidates(proj, op, limits):
    """Same public generation seam and generate-only settings as the original comparison."""
    import schemathesis
    from hypothesis import Phase, given, seed, settings
    from schemathesis import GenerationMode

    # The original parser proves references are local and bounded before either loader sees them.
    load_openapi(proj.document)
    schema = schemathesis.openapi.from_dict(json.loads(proj.document))
    source = schema[op.path][op.method]
    started = time.perf_counter()
    result = {}
    modes = {"positive": GenerationMode.POSITIVE, "negative": GenerationMode.NEGATIVE}
    for label, mode in modes.items():
        limit = limits["count_by_mode"][label]
        rows = []

        @seed(limits["seed"])
        @settings(max_examples=limit, phases=[Phase.generate], database=None,
                  deadline=None, derandomize=True)
        @given(source.as_strategy(generation_mode=mode))
        def collect(case):
            if len(rows) >= limit:
                raise RuntimeError("POC_CANDIDATE_CAP_EXCEEDED")
            row = {}
            if isinstance(case.body, dict):
                row["body"] = deepcopy(case.body)
            elif case.media_type is not None:
                row["unsupported_body_type"] = type(case.body).__name__
            if case.query:
                row["query"] = deepcopy(dict(case.query))
            # Path data for these fixtures is a resource identifier. Keep it out of projection.
            rows.append(row)

        try:
            collect()
        except Exception as exc:
            # Preserve generator errors/shortfalls without changing the sample or retrying it.
            result[label] = dict(rows=rows, error=type(exc).__name__)
        else:
            result[label] = dict(rows=rows, error=None)
    return result, round((time.perf_counter() - started) * 1000, 3)


def bound_path(check, op):
    # Used for data-only schema checks. These values never enter an executable plan.
    params, _ = request_schemas(op, "application/json" if op.request_body else None)
    return {rule.name: params["path"][rule.name].get("minimum", "fixture-id")
            for rule in check.binding_rules if rule.location == "path_parameters"}


def match_candidates(proj, policy, subject, raw, known):
    op = operation(proj, policy)
    path = bound_path(policy, op)
    media = "application/json" if op.request_body else None
    expected_slot = subject.get("open_slot")
    audit = {"positive": [], "negative": []}
    option = None
    variants = []
    for row in raw["positive"]["rows"]:
        supplied = deepcopy(known)
        try:
            if expected_slot:
                location, name = expected_slot
                value = row[location][name]
                supplied.setdefault(location, {})[name] = value
                rule = next(r for r in policy.binding_rules if (r.location, r.name)
                            == (location, name))
                validate_value(value, rule.literal_schema)
            validate_request(op, path, supplied.get("query", {}), supplied.get("body", {}),
                             media, headers=supplied.get("headers", {}))
            if not expected_slot:
                # A conversion-free equivalent candidate must preserve the frozen literals.
                agreed = all(same_wire_value(row.get(loc, {}), known.get(loc, {}))
                             for loc in ("body", "query"))
                if not agreed:
                    audit["positive"].append("FROZEN_LITERAL_DIFFERENCE")
                    continue
        except KeyError:
            audit["positive"].append("OPEN_SLOT_MISSING")
        except ValueError as exc:
            audit["positive"].append(diagnostic(exc))
        else:
            audit["positive"].append("ACCEPTED")
            if option is None:
                option = supplied
    baseline = option if expected_slot else known
    if baseline is not None:
        try:
            query, body = mutate_request(policy.negative, op, path, baseline.get("query", {}),
                                         baseline.get("body", {}), media)
        except ValueError as exc:
            audit["negative"] = [diagnostic(exc)] * len(raw["negative"]["rows"])
            return baseline, audit, 0
        for row in raw["negative"]["rows"]:
            exact = ("unsupported_body_type" not in row and
                     same_wire_value(row.get("body", {}), body)
                     and same_wire_value(row.get("query", {}), query))
            audit["negative"].append("EXACT_FIXED_VARIANT" if exact else "VARIANT_MISMATCH")
            if exact:
                variants.append(row)
    else:
        audit["negative"] = ["NO_VALID_BASELINE"] * len(raw["negative"]["rows"])
    return baseline, audit, len(variants)


def new_run(proj, scenario):
    return RunReport(scenario_sha256=fingerprint(scenario.model_dump()),
                     contract_sha256=fingerprint(proj.contract.model_dump()),
                     environment=proj.target.environment, target_origin=proj.target.base_url)


def review_plan(db, proj, scenario, *, approve):
    """Use the original review persistence, with a PoC stand-in for the Graph interrupt."""
    identity = proj.identity()
    plan_digest = fingerprint(scenario.model_dump())
    db.create("task", OWNER, "frozen", proj.registration.id, identity, plan_digest, "RUNNING", {})
    operations = group_operations(proj, [scenario], ())
    if not operations:
        return None
    review = dict(review_id="review", task_id="task", status="WAITING_APPROVAL", version=1,
                  allowed_decisions=["approve", "reject"], operations=operations,
                  authority=identity, plan_sha256=plan_digest)
    db.save_durable_state("task", OWNER, {"plan_sha256": plan_digest}, review=review)
    db.transition("task", OWNER, "RUNNING", "INTERRUPTED")
    reviews = HumanReviews(db)
    if not approve:
        return reviews.list("task", OWNER, "review")["status"]
    current, changed = reviews.decide("task", OWNER, review, ReviewDecision(
        decision="approve", version=1))
    if (not changed or current["status"] != "APPROVED"
            or current["authority"] != proj.identity()
            or current["plan_sha256"] != fingerprint(scenario.model_dump())
            or current["operations"] != group_operations(proj, [scenario], ())):
        raise RuntimeError("POC_REVIEW_BINDING_CHANGED")
    db.transition("task", OWNER, "INTERRUPTED", "RUNNING")
    return current["status"]


def recording_transport(proj, script, calls, *, failure=None):
    """Pre-recorded responses, with independent method/path/identity checks."""
    identity = {f"Bearer {IDENTITIES[config.bearer_env]}": role
                for role, config in proj.target.roles.items()}
    origin = httpx.URL(proj.target.base_url)

    def reply(request):
        index = len(calls)
        if index >= len(script) or request.url.netloc != origin.netloc or request.url.scheme != (
                origin.scheme) or "cookie" in request.headers:
            raise RuntimeError("POC_UNEXPECTED_TRANSPORT_DISPATCH")
        expected = script[index]
        header = request.headers.get("Authorization")
        role = identity.get(header, "unrecognized") if header is not None else None
        actual = dict(method=request.method, path=request.url.path, role=role)
        if any(value != expected[key] for key, value in actual.items()):
            raise RuntimeError("POC_REQUEST_ROUTE_OR_IDENTITY_MISMATCH")
        correlation = request.headers.get("X-Lab-Correlation", request.headers.get("X-Audit-Key"))
        calls.append(dict(**actual, query=dict(request.url.params),
                          body=json.loads(request.content) if request.content else None,
                          correlation=correlation))
        if failure == "unknown" and request.method == "POST":
            raise httpx.ReadTimeout("synthetic missing response", request=request)
        body = expected.get("response")
        if failure == "wrong_code" and body and "code" in body:
            body = {"code": "SYNTHETIC_WRONG_CODE"}
        return httpx.Response(expected["status"], json=body) if body is not None else (
            httpx.Response(expected["status"]))

    return httpx.MockTransport(reply)


async def execute(proj, scenario, directory, script, *, approve=True, budget=12, failure=None):
    directory.mkdir(parents=True)
    db = TaskStore(directory / "tasks.sqlite3")
    calls = []
    status = review_plan(db, proj, scenario, approve=approve)
    if status == "WAITING_APPROVAL":
        return dict(outcome="HUMAN_APPROVAL_REQUIRED", approval=status, calls=calls,
                    ledger_requests=0)
    ledger = SuiteLedger(budget, 0, 0)
    transport = BudgetTransport(recording_transport(proj, script, calls, failure=failure), ledger)
    original = new_run(proj, scenario)
    scope = StepScope(db, "task", OWNER, "tool", "group", "case", original.run_id,
                      proj.identity(), original.scenario_sha256)

    async def invoke():
        journal = StepJournal(scope)
        store = ProgressStore(directory / "reports", lambda *_: None, step_journal=journal)
        snapshot = original.model_copy(deep=True)
        if journal.prior:
            snapshot.revision = store.read(snapshot.run_id).revision
        return await Executor(proj.target, proj.contract, store, transport, IDENTITIES,
                              proj.registry, step_journal=journal).run(scenario, report=snapshot)

    try:
        result = await invoke()
    except StepRecoveryBlocked as exc:
        interrupted = dict(outcome=str(exc), approval=status, calls=calls,
                           ledger_requests=ledger.requests,
                           phases=[row["phase"] for row in tool_attempts(db, "task", "tool")])
        if failure == "unknown":
            count = len(calls)
            try:
                await invoke()
            except StepRecoveryBlocked as resume:
                interrupted.update(resume=str(resume), replay_dispatches=len(calls)-count)
        return interrupted
    return dict(outcome=result.outcome.value, approval=status, calls=calls,
        run_id=result.run_id, ledger_requests=ledger.requests,
        policy_authorized=result.policy_authorized, completed=result.completed,
        report_diagnostic=result.diagnostic.code if result.diagnostic else result.reason,
        policies=step_policies(scenario, (s.step_id for s in result.steps)),
        steps=[dict(step_id=s.step_id, operation=s.operation_id,
                    status=s.attempts[-1].status_code if s.attempts else None,
                    outcome=s.outcome.value, reason=s.reason,
                    checks=[dict(kind=c.kind, expected=c.expected, actual=c.actual, passed=c.passed)
                            for c in s.checks]) for s in result.steps],
        lifecycle=result.resource_lifecycle)


def binding_evidence(scenario, wanted):
    location, name, selector = wanted
    source, *consumers = scenario.steps
    return all(step.role == source.role
               and (binding := getattr(step, location).get(name)) is not None
               and binding.from_step == source.id and binding.select == selector
               for step in consumers)


def step_policies(scenario, executed_ids):
    by_id = {step.id: step.policy_id for step in scenario.steps}
    return [by_id[identifier] for identifier in executed_ids]


def oracle_match(receipt, scenario, expected):
    if (receipt["outcome"] != "PASS" or receipt.get("policies") != expected["policies"]
            or len(receipt["calls"]) != len(expected["calls"])):
        return False
    if "binding" in expected and (not binding_evidence(scenario, expected["binding"])
            or not receipt["lifecycle"]
            or receipt["lifecycle"][0]["status"] != "CLEANED"):
        return False
    location, name, shape, *values = expected["change"]
    check = receipt["calls"][next(i for i, call in enumerate(expected["calls"])
                                  if call["status"] == 422)]
    values_sent = check[location]
    if shape == "missing" and name in values_sent:
        return False
    if shape == "value":
        value = str(values[0]) if location == "query" else values[0]
        if name not in values_sent or encoded(values_sent[name]) != encoded(value):
            return False
    if len(expected["calls"]) == 3:
        first, write_call, last = receipt["calls"]
        if not write_call["correlation"] or (
                first["query"] != last["query"] or
                next(iter(first["query"].values()), None) != write_call["correlation"]):
            return False
    return True


# These two frozen human-facing labels predate the exact owning code lookup. Preserve both
# labels and raw production diagnostics. The mapping specifies matching failure semantics only.
REJECTION_CODES = {
    "External OpenAPI references are not allowed": "OPENAPI_LOCAL_REFERENCE_REQUIRED",
    "UNAPPROVED_INPUT_BINDING": "PLANNING_REFERENCE_MISMATCH",
}


def reference_control(before, check, public, *, ambiguous):
    other = deepcopy(before[0])
    other["id"] = "other"
    argument = next(item for item in check["arguments"] if item.get("from_step"))
    if ambiguous:
        argument["from_step"] = "unselected"
        # The original compiler's failure explicitly lists both legal producer IDs.
        compile_case([*before, other], check, public)
        return
    argument["from_step"] = "other"
    nodes = [*before, other, check]
    instances = [ActionInstance(instance_id=node["id"], operation_id=node["operation_id"],
        role=node["role"], source_id=f"poc_{i}", source_quote="Frozen source choice",
        source_start=i, policy_id=node["policy_id"]) for i, node in enumerate(nodes)]
    instances[-1].arguments = [InstanceArgument(location=argument["location"],
        name=argument["name"], from_instance="prepare", select=argument["select"])]
    ir = PlanningIR(instances=instances, order=[node["id"] for node in nodes])
    compile_design(ir, dict(name="bound-source", steps=nodes),
                   {**public, "exact_instance_step_ids": True})


async def control(proj, subject, mode, value, directory, script):
    catalog = policies(proj)
    policy = catalog[subject["id"]]
    _, public = context(proj, policy)
    before, check = trusted_nodes(subject, catalog, subject["proposal"])
    calls = []
    try:
        if mode == "unapproved_value":
            select_negative_approval(policy.negative, operation(proj, policy), value, override=True)
        elif mode == "external_ref":
            document = json.loads(proj.document)
            document["components"]["schemas"]["AssetInput"] = {"$ref": "https://fixture.invalid/x"}
            load_openapi(encoded(document))
        elif mode in {"ambiguous_producer", "different_instance"}:
            reference_control(before, check, public, ambiguous=mode == "ambiguous_producer")
        elif mode == "wrong_role":
            compile_case(before, {**check, "role": value}, public)
        elif mode == "unapproved_policy":
            compile_case(before, {**check, "policy_id": "unreviewed"}, public)
        elif mode == "missing_producer":
            compile_case([], check, public)
        else:
            case = compile_case(before, check, public)
            if mode == "extra_change":
                scenario = case.plan.scenario()
                scenario.steps[-1].body["extra"] = ArgumentBinding(value="extra")
                Executor(proj.target, proj.contract, ReportStore(directory), None,
                         IDENTITIES, proj.registry).validate(scenario)
            else:
                scenario = preflight(proj, case, ReportStore(directory / "preflight"),
                                     cleanup=(mode != "missing_cleanup"))
                if mode == "no_approval":
                    return await execute(proj, scenario, directory / "dispatch", script,
                                         approve=False)
    except ValueError as exc:
        return dict(outcome=diagnostic(exc), calls=calls, ledger_requests=0,
                    detail=getattr(getattr(exc, "diagnostic", None), "detail", None))
    return dict(outcome="UNEXPECTED_ACCEPT", calls=calls, ledger_requests=0)


def type_control(proj, case, store, integer_probe):
    op = operation(proj, policies(proj)[case["id"]])
    _, schema = request_schemas(op, "application/json")
    quantity = schema["properties"]["quantity"]
    comparisons = []
    for value in integer_probe:
        round_tripped = json.loads(encoded(value))
        try:
            validate_value(round_tripped, quantity)
        except ValueError as exc:
            result = diagnostic(exc)
        else:
            result = "ACCEPTED"
        comparisons.append(dict(wire=encoded(value).decode(), type=type(round_tripped).__name__,
                                schema_valid=Draft202012Validator(quantity).is_valid(value),
                                original_validator=result))
    # Decode+compile path retains the strict policy type as well as the direct scalar check.
    policy = policies(proj)[case["id"]]
    original = deepcopy(case["proposal"])
    original["body"]["quantity"] = integer_probe[-1]
    before, step = trusted_nodes(case, policies(proj), original)
    try:
        plan = compile_case(before, step, context(proj, policy)[1])
        preflight(proj, plan, store)
    except ValueError as exc:
        compiled_outcome = diagnostic(exc)
    else:
        compiled_outcome = "UNEXPECTED_ACCEPT"
    return dict(comparisons=comparisons, compiled_outcome=compiled_outcome,
                conversion="none", dispatches=0)


async def run_position(arm, proj, subject, limits, expected, directory, network_attempts):
    policy = policies(proj)[subject["id"]]
    group, public = context(proj, policy)
    metrics = dict(design_calls=0, request_bytes=[], model_fields=0, request_model_arguments=0,
                   compiled_by="construct_plan")
    result = dict(arm=arm, open_slot=subject.get("open_slot"), resolved_open_slot=False,
                  metrics=metrics, calls=[], ledger_requests=0)
    directory.mkdir(parents=True)
    store = ReportStore(directory / "preflight")
    try:
        if arm == "A0":
            case, metrics = await a0(proj, subject, policy, group)
            result["metrics"] = metrics
        else:
            baseline = subject["fixed"]
            if arm == "B1":
                with no_network(network_attempts, "generation"):
                    raw, elapsed = raw_candidates(proj, operation(proj, policy), limits)
                baseline, audit, exact = match_candidates(proj, policy, subject, raw, baseline)
                result["generation"] = dict(elapsed_ms=elapsed,
                    results={mode: {**row, "outcomes": audit[mode],
                                    "shortfall": limits["count_by_mode"][mode]-len(row["rows"])}
                             for mode, row in raw.items()}, exact_fixed_variants=exact)
            if subject.get("open_slot") and (arm == "C0" or baseline is None):
                return {**result, "outcome": "OPEN_INPUT_SLOT", "oracle_pass": False}
            before, step = trusted_nodes(subject, policies(proj), baseline)
            case = compile_case(before, step, public)
        scenario = preflight(proj, case, store)
        result["resolved_open_slot"] = bool(subject.get("open_slot"))
    except ValueError as exc:
        return {**result, "outcome": diagnostic(exc), "oracle_pass": False}
    result["plan"] = scenario.model_dump(mode="json")
    result.update(await execute(proj, scenario, directory / "run", expected["calls"]))
    result["oracle_pass"] = oracle_match(result, scenario, expected)
    return result


async def run(output, limits, oracle):
    attempts = []
    with no_network(attempts, "experiment"):
        projects = {"A": project(PARCEL), "B": project(FIXTURES / "assets.json")}
        positions = {}
        for pid, subject in limits["subjects"].items():
            positions[pid] = [
                await run_position(arm, projects[pid[0]], subject, limits,
                                   oracle["approved"][pid], output / pid / arm, attempts)
                for arm in ARMS
            ]
            write(output / pid / "receipt.json", data_evidence(positions[pid]))
        # All arms share these gates. The same blockers run once per frozen position.
        for pid, (source, mode, value) in limits["controls"].items():
            expected = oracle["controls"][pid]
            directory = output / pid
            directory.mkdir()
            result = await control(projects[pid[0]], limits["subjects"][source], mode,
                                   value, directory, oracle["approved"][source]["calls"])
            result.update(expected=expected, arms=list(ARMS),
                oracle_pass=(result["outcome"] == REJECTION_CODES.get(expected, expected)
                             and not result["calls"]))
            positions[pid] = result
            write(directory / "receipt.json", data_evidence(result))
        aux = oracle["auxiliary"]
        a04 = limits["subjects"][aux["unknown_write"][0]]
        b04 = limits["subjects"][aux["budget"][0]]
        plans = {}
        for key, proj, subject in (("A04", projects["A"], a04), ("B04", projects["B"], b04)):
            before, check = trusted_nodes(subject, policies(proj), subject["fixed"])
            _, public = context(proj, policies(proj)[subject["id"]])
            plans[key] = preflight(proj, compile_case(before, check, public),
                                   ReportStore(output / f"{key}-control-preflight"))
        b03 = limits["subjects"][aux["same_status_wrong_code"][0]]
        before, check = trusted_nodes(b03, policies(projects["B"]), b03["fixed"])
        _, public = context(projects["B"], policies(projects["B"])[b03["id"]])
        null_case = preflight(projects["B"], compile_case(before, check, public),
                              ReportStore(output / "B03-control-preflight"))
        auxiliary = dict(
            integer=type_control(projects["A"], limits["subjects"][aux["integer"][0]],
                                 ReportStore(output / "integer-control"), aux["integer"][1:]),
            same_status_wrong_code=await execute(projects["B"], null_case,
                output / "B03-wrong-code", oracle["approved"]["B03"]["calls"],
                failure="wrong_code"),
            reachable_wrong_code=await execute(projects["B"], plans["B04"],
                output / "B04-wrong-code", oracle["approved"]["B04"]["calls"],
                failure="wrong_code"),
            budget=await execute(projects["B"], plans["B04"], output / "B04-budget",
                                 oracle["approved"]["B04"]["calls"], budget=aux["budget"][1]),
            unknown_write=await execute(projects["A"], plans["A04"], output / "A04-unknown",
                                        oracle["approved"]["A04"]["calls"], failure="unknown"))
    return data_evidence(dict(versions=limits["versions"], python=limits["python"],
        network_attempts=attempts, real_model_calls=0, positions=positions, auxiliary=auxiliary))


def cold_audit(directory, oracle):
    """Validate already saved receipts with no planner, generation or Executor calls."""
    data = load(directory / "summary.json")
    assessments = {}
    for pid, rows in data["positions"].items():
        if not isinstance(rows, list):
            expect = oracle["controls"][pid]
            assessments[pid] = dict(actual=rows["outcome"],
                pass_=rows["outcome"] == REJECTION_CODES.get(expect, expect)
                and not rows["calls"] and rows["ledger_requests"] == 0)
            continue
        assessment = {}
        for row in rows:
            arm = row["arm"]
            matched = False
            if row["outcome"] == "PASS":
                out = directory / pid / arm / "run"
                scenario = Scenario.model_validate(row["plan"])
                saved = ReportStore(out / "reports").read(row["run_id"])
                uri = (out / "tasks.sqlite3").resolve().as_uri() + "?mode=ro&immutable=1"
                with sqlite3.connect(uri, uri=True) as db:
                    db.row_factory = sqlite3.Row
                    proof = db.execute(
                        "SELECT identity, step_id FROM http_execution "
                        "WHERE run_id=? AND phase='EVIDENCE_COMMITTED'", (row["run_id"],)
                    ).fetchall()
                assert saved.scenario_sha256 == fingerprint(scenario.model_dump())
                assert all(json.loads(receipt["identity"])["plan_sha256"]
                           == saved.scenario_sha256 for receipt in proof)
                by_step = {step.step_id: step for step in saved.steps}
                assert all(json.loads(receipt["identity"])["policy_sha256"] ==
                           by_step[receipt["step_id"]].policy_sha256 for receipt in proof)
                assert len(proof) == len(saved.steps) == row["ledger_requests"]
                assert saved.policy_authorized and saved.outcome.value == row["outcome"]
                actual = {**row, "policies": step_policies(scenario, by_step)}
                matched = oracle_match(actual, scenario, oracle["approved"][pid])
            assessment[arm] = dict(actual=row["outcome"], pass_=matched,
                                   transport=len(row["calls"]))
        assessments[pid] = assessment
    return dict(assessments=assessments, network_attempts=data["network_attempts"],
                model_calls=data["real_model_calls"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--audit", action="store_true", help="Read and verify saved results")
    args = parser.parse_args()
    limits = checked_inputs()
    if args.audit:
        record = cold_audit(args.output, load_oracle())
        write(args.output / "cold-audit.json", record)
        print(json.dumps(record, ensure_ascii=False, indent=2))
        raise SystemExit
    args.output.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(run(args.output, limits, load_oracle()))
    write(args.output / "summary.json", result)
    for pid, rows in result["positions"].items():
        if isinstance(rows, list):
            print(pid, [(r["arm"], r["outcome"], r["oracle_pass"], len(r["calls"])) for r in rows])
        else:
            print(pid, rows["outcome"], rows["oracle_pass"], len(rows["calls"]))
    print("auxiliary", {key: item.get("outcome", item.get("compiled_outcome"))
                        for key, item in result["auxiliary"].items()})
    if result["network_attempts"]:
        raise SystemExit("PoC attempted forbidden network access")
