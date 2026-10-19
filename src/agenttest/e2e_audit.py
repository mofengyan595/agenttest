"""Cold E2E attribution from sealed plans and public contexts; no new model calls."""

import json
from pathlib import Path

from agenttest.dsl import Scenario
from agenttest.eval_v2.archive import read_json
from agenttest.eval_v2.scoring import score_intent
from agenttest.evidence_evaluation import verify_evidence
from agenttest.report import ReportStore
from agenttest.task_contract import TaskContract


def audit_e2e(archive: Path) -> dict:
    verified = verify_evidence(archive)
    frozen, result = (read_json(archive / n) for n in ("freeze.json", "results.json"))
    if frozen["split"] != "end_to_end":
        raise ValueError("Expected sealed natural-language end-to-end archive")
    rows = []
    for row in result["results"]:
        key = row["case_id"]
        task = TaskContract.model_validate_json(json.dumps(frozen["tasks"][key]))
        plan = Scenario.model_validate_json(json.dumps(row["scenario"])) \
            if row.get("scenario") else None
        score = score_intent(task.requirements, plan)
        report = ReportStore(archive / "reports").read(row["run_id"])
        public = frozen["planning_contexts"][key]
        missing_purposes = [p["id"] for p in public["policies"] if not p.get("purpose")]
        rows.append(dict(case_id=key, record=f"reports/{report.run_id}/report.json",
            intent_diagnostics=score.diagnostics, outcome=report.outcome.value,
            reason=report.reason, public_policies_without_purpose=missing_purposes,
            binding_rules_visible=all("binding_rules" in p for p in public["policies"]),
            task_contract_exposed=bool({"requirements", "task_contract"} & public.keys()),
            proposals=[d.proposal for d in report.feedback.decisions]
                if report.feedback else [],
            effective_evidence=row["grade"]["effective_evidence"],
            strict_correct=row["grade"]["strict_correct"],
            internal_reasoning="NOT_RECORDED_CANNOT_INFER"))
    return dict(verification=verified, classification="DEVELOPMENT_ONLY",
                review_status="UNREVIEWED", rows=rows)
