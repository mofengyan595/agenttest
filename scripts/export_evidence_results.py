"""Export allowlisted metrics and report locators after cold verification; no model calls."""

import argparse
import copy
import json
from pathlib import Path

from agenttest.eval_v2.archive import read_json
from agenttest.evaluation import save_evaluation
from agenttest.evidence_attribution import audit_attribution
from agenttest.report import ReportStore, fingerprint


def export_results(archive):
    attribution = audit_attribution(archive)
    frozen, original = (read_json(archive / n) for n in ("freeze.json", "results.json"))
    result = copy.deepcopy({k: v for k, v in original.items() if k != "results"})
    for name, group in result["groups"].items():
        group["unsupported_decision_proxy_v1"] = group.pop("inappropriate_assertion")
        group.update({k: v for k, v in attribution["groups"][name].items()
                      if k not in {"denominator", "observed", "active_abstention"}})
    attributed = {(r["case_id"], r["round"], r["arm"]): r for r in attribution["results"]}
    store, rows = ReportStore(archive / "reports"), []
    for row in original["results"]:
        if not row.get("run_id"):
            rows.append(row)
            continue
        report = store.read(row["run_id"])
        public = {k: v for k, v in row.items() if k != "scenario"}
        public["grade"] = dict(public["grade"])
        public["grade"]["unsupported_decision_proxy_v1"] = public["grade"].pop(
            "inappropriate_assertion")
        public["attribution"] = attributed[(row["case_id"], row["round"], row["arm"])]
        public["execution"] = {k: getattr(report, k) for k in (
            "dsl_valid", "policy_authorized", "intent_verified", "task_mode", "task_satisfied")}
        public["diagnostic"] = report.diagnostic.code if report.diagnostic else None
        public["feedback_state"] = report.feedback.state if report.feedback else None
        rows.append(public)
    return {**result, "attribution_version": attribution["attribution_version"],
        "provenance": {"archive": archive.as_posix(), "git": frozen["git"],
            "seal_sha256": fingerprint(read_json(archive / "archive.json")),
            "frozen_at": frozen["frozen_at"], "prompts": frozen["prompts"],
            "model": frozen["model"], "max_model_calls": frozen["max_model_calls"],
            "max_tokens": frozen["max_tokens"], "scorer_sha256": frozen["scorer_sha256"]},
        "strict_scores_unchanged": True, "results": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export_results(args.archive)
    save_evaluation(result, args.output)
    print(json.dumps({"positions": len(result["results"]), "new_model_calls": 0,
                      "strict_scores_unchanged": True}))
