"""Cold audit of saved proposals and diagnostics; no model calls or inferred model reasoning."""

import argparse
from collections import Counter
from pathlib import Path

from agenttest.eval_v2.archive import inventory, read_json
from agenttest.evaluation import save_evaluation


def signature(step):
    return tuple(step.get(k) for k in ("operation_id", "role", "policy_id"))


def audit(root):
    original = read_json(root / "archive.json")["files"]
    if original != inventory(root):
        raise ValueError("ORIGINAL_ARCHIVE_CHANGED")
    rows, budget_exclusions = [], []
    for folder in sorted(root.glob("position-*")):
        product = read_json(folder / "product.json")
        plans = read_json(folder / "plans.json")
        for case in product["cases"]:
            if case.get("reason") == "SUITE_BUDGET_OR_USAGE_STOP":
                budget_exclusions.append(dict(position=folder.name, case_id=case["case_id"],
                                              requested_goals=case["requested_goals"]))
            if not case.get("run_id"):
                continue
            report = read_json(folder / "reports" / case["run_id"] / "report.json")
            expected = case["required_plan"]
            actual = (plans.get(case["run_id"]) or {}).get("steps", [])
            expected_set = Counter(map(signature, expected))
            actual_set = Counter(map(signature, actual))
            extra, absent = actual_set - expected_set, expected_set - actual_set
            mapped = {s["key"]: [a for a in actual if signature(a) == signature(s)]
                      for s in expected}
            dependency_errors, order_errors, state_alternatives = [], [], []
            for s in expected:
                matches = mapped[s["key"]]
                if len(matches) != 1:
                    continue
                a = matches[0]
                for arg in s["arguments"]:
                    if "source" not in arg:
                        continue
                    sources = mapped.get(arg["source"], [])
                    slot = arg["location"] + "." + arg["name"]
                    if len(sources) != 1 or (
                            a["dependencies"].get(slot) != sources[0]["step_id"]
                            or a["selectors"].get(slot) != arg["selector"]):
                        dependency_errors.append(dict(policy_id=s["policy_id"], slot=slot))
                for following in s["before"]:
                    later = mapped.get(following, [])
                    if len(later) == 1 and actual.index(a) >= actual.index(later[0]):
                        order_errors.append([s["key"], following])
            for s in expected:
                if mapped[s["key"]]:
                    continue
                for a in actual:
                    if (s["operation_id"], s["role"]) == (a["operation_id"], a["role"]):
                        state_alternatives.append(dict(expected=s["policy_id"],
                                                       proposed=a["policy_id"]))
            rows.append(dict(position=folder.name, run_id=case["run_id"],
                requested_goals=case["requested_goals"],
                expected_steps=len(expected), proposed_steps=len(actual),
                diagnostic=report.get("diagnostic"),
                extra=[dict(operation_id=k[0], role=k[1], policy_id=k[2], count=v)
                       for k, v in extra.items()],
                missing=[dict(operation_id=k[0], role=k[1], policy_id=k[2], count=v)
                         for k, v in absent.items()],
                dependency_errors=dependency_errors, order_errors=order_errors,
                alternative_policies_for_missing_goals=state_alternatives))
    if original != inventory(root):
        raise ValueError("ORIGINAL_ARCHIVE_CHANGED_DURING_AUDIT")
    return dict(schema_version="agenttest.saved-plan-audit.v1", real_model_calls=0,
        original_archive_verified=True, reports=len(rows),
        runs_with_extra_steps=sum(bool(r["extra"]) for r in rows),
        runs_with_missing_steps=sum(bool(r["missing"]) for r in rows),
        runs_with_wrong_dependencies=sum(bool(r["dependency_errors"]) for r in rows),
        runs_with_wrong_order=sum(bool(r["order_errors"]) for r in rows),
        runs_with_alternative_policies=sum(bool(r["alternative_policies_for_missing_goals"])
                                          for r in rows),
        budget_exclusions=budget_exclusions, rows=rows,
        inference_limit="Only saved proposals, exact scope and diagnostics are audited; "
            "no internal model reasoning is available.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("AUDIT_OUTPUT_EXISTS")
    result = audit(args.archive)
    save_evaluation(result, args.output)
    print({k: v for k, v in result.items() if k not in {"rows", "budget_exclusions"}})
