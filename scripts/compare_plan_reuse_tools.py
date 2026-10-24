"""Common create-operation subset only; no NL scores or hidden fault/reference access."""

import argparse
import asyncio
import importlib.metadata
import json
from copy import deepcopy
from pathlib import Path

from compare_contract_tools import data_evidence, generated_data
from trusted_validation_support import target_runtime

from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import inventory, read_json
from agenttest.evaluation import save_evaluation
from agenttest.executor import Executor
from agenttest.goal_suite import BudgetTransport, SuiteLedger
from agenttest.project_goals import load_goal_project
from agenttest.report import ReportStore


def approved_generation_document(project, policy_id, operation):
    """Intersect public literal approvals with the public contract; no scoring labels."""
    document = json.loads(project.document)
    policy = next(p for p in project.registry.definitions() if p.id == policy_id)
    content = document["paths"][operation.path][operation.method.lower()]["requestBody"][
        "content"]["application/json"]
    rules = [r for r in policy.binding_rules if r.location == "body"]
    if any(r.literal_schema is None or r.source_policy_id for r in rules):
        raise ValueError("COMMON_SUBSET_REQUIRES_LITERAL_BODY")
    overlay = dict(type="object", required=[r.name for r in rules],
                   properties={r.name: r.literal_schema for r in rules})
    if policy.closed_arguments:
        overlay["additionalProperties"] = False
    content["schema"] = dict(allOf=[content["schema"], overlay])
    return json.dumps(document).encode()


async def compare(preparation, output, *, with_generator=True, rounds=3):
    import httpx

    requests = read_json(preparation / "public-requests.json")
    fixtures = read_json(Path(__file__).resolve().parents[1] /
                         "examples/plan-reuse/tool-fixtures.json")
    rows = []
    for round_number in range(1, rounds + 1):
        # Flow diagnosis and unknown NL semantics are outside this common contract subset.
        for request in requests[:4]:
            project = load_goal_project(preparation / "projects" / request["project"] /
                                        "registered.project.json")
            fixture = fixtures[request["project"]]
            base = fixture["base_policy"]
            policies = [base, *sorted(p.id for p in project.registry.definitions()
                                      if p.negative and p.negative.base_policy_id == base)]
            template = Scenario.model_validate(dict(name="prewritten common subset fixture",
                                                    steps=fixture["steps"]))
            operation = next(o for o in project.contract.operations
                             if o.operation_id == template.steps[-1].operation_id)
            generation_document = approved_generation_document(project, base, operation)
            generated = (generated_data(generation_document, len(policies),
                operation_path=operation.path, method=operation.method) if with_generator else None)
            for arm in (("fixed_pytest", "schemathesis") if generated else ("fixed_pytest",)):
                app, env = target_runtime(project)
                budget = len(template.steps) * len(policies)
                ledger = SuiteLedger(budget, 0, 0)
                transport = BudgetTransport(httpx.ASGITransport(app=app), ledger)
                folder = output / f"round-{round_number}-{request['id']}-{arm}"
                store, cases = ReportStore(folder), []
                for index, policy in enumerate(policies):
                    plan = template.model_copy(deep=True)
                    plan.steps[-1].policy_id = policy
                    if arm == "schemathesis":
                        body = deepcopy(generated["positive"][index])
                        from agenttest.dsl import ArgumentBinding

                        plan.steps[-1].body = {k: ArgumentBinding(value=v) for k, v in body.items()}
                    report = await Executor(project.target, project.contract, store,
                        transport, env, project.registry).run(plan)
                    last = report.steps[-1] if len(report.steps) == len(plan.steps) else None
                    status = last.attempts[-1].status_code if last and last.attempts else None
                    cases.append(dict(policy=policy, run_id=report.run_id,
                        executed=status is not None, outcome=report.outcome.value,
                        schema_passed=status == (201 if index == 0 else 422),
                        business_passed=report.outcome.value == "PASS",
                        diagnostic=report.diagnostic.code if report.diagnostic else report.reason))
                rows.append(dict(round=round_number, case_id=request["id"],
                    project=request["project"],
                    arm=arm, request_budget=budget, actual_requests=ledger.requests,
                    common_denominator=len(policies), cases=cases,
                    schema_passed=sum(c["schema_passed"] for c in cases),
                    business_passed=sum(c["business_passed"] for c in cases),
                    raw_data_evidence=data_evidence(generated) if generated else None))
    summary = dict(schema_version="agenttest.plan-reuse.tools.v1",
        classification="DEVELOPMENT_ONLY/UNREVIEWED", rows=rows, real_model_calls=0,
        generator_version=importlib.metadata.version("schemathesis") if with_generator else None,
        native_negative_requests=0, labels_or_fault_implementation_read=False,
        mutation_contribution="Existing approved deterministic rules in both arms",
        generation_constraints="Public OpenAPI intersected with the SAME approved literal schemas "
            "and closed-argument rules; no labels, assertions or implementation in generator input",
        subset="PR01..PR04 create-operation normal and approved negative inputs only",
        excluded_capabilities="PR05 flow diagnosis and PR06 NL clarification are not graded here",
        repetition="Three repeated rounds; same schema/seed and structures, not new samples",
        human_time_seconds=None, fixture_file="examples/plan-reuse/tool-fixtures.json",
        fixture_maintenance="Explicit body/auth fixture per project plus approved-rule adapter",
        profile_inventory=inventory(preparation / "projects"))
    save_evaluation(summary, output / "summary.json")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = asyncio.run(compare(args.preparation, args.output))
    print(json.dumps(dict(rows=len(result["rows"]), real_model_calls=0)))
