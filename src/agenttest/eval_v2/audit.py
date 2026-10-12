"""Offline regrading from immutable reports and redacted structural plan evidence."""

from agenttest.eval_v2.comparison import _read
from agenttest.eval_v2.models import CaseResult
from agenttest.eval_v2.scoring import score_report
from agenttest.report import ReportStore, fingerprint


def rescore(suite, directory):
    frozen = _read(directory / "freeze.json")
    if fingerprint(suite.model_dump()) != frozen["suite_sha256"]:
        raise ValueError("Cannot rescore against different tasks/labels")
    store, results = ReportStore(directory / "reports"), []
    for case in suite.cases:
        row = CaseResult.model_validate_json((directory / (case.case_id + ".json")).read_bytes())
        if not row.plan_valid:
            results.append(
                {
                    "case_id": case.case_id,
                    "regraded": False,
                    "reason": "NO_VALIDATED_PLAN_EVIDENCE",
                    "original_classification": row.classification,
                }
            )
            continue
        if row.run_id is None or row.plan_evidence is None:
            raise ValueError("Missing archived evidence")
        report = store.read(row.run_id)
        if (
            fingerprint(report.model_dump()) != row.report_sha256
            or row.plan_evidence.scenario_sha256 != row.plan_sha256
        ):
            raise ValueError("Archived report/plan identity mismatch")
        score = score_report(case, report, row.plan_evidence)
        if score["intent"] is not None:
            score["intent"] = score["intent"].model_dump()
        results.append({"case_id": case.case_id, "regraded": True, **score})
    return {
        "source": "archived_evidence",
        "model_calls": 0,
        "business_http_requests": 0,
        "suite_sha256": frozen["suite_sha256"],
        "results": results,
    }
