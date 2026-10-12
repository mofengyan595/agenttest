"""Offline regrading from immutable reports and redacted structural plan evidence."""

from agenttest.eval_v2 import EVALUATOR_VERSION
from agenttest.eval_v2.archive import UnverifiableArchive, verify_archive
from agenttest.eval_v2.models import CaseResult
from agenttest.eval_v2.scoring import score_report
from agenttest.report import ReportStore, fingerprint


def rescore(suite, directory):
    try:
        original, frozen = verify_archive(directory)
    except UnverifiableArchive:
        return {"audit_status": "UNVERIFIABLE_LEGACY_OR_INCOMPLETE_ARCHIVE",
                "model_calls": 0, "business_http_requests": 0, "results": []}
    if fingerprint(suite.model_dump()) != frozen["suite_sha256"]:
        raise ValueError("Cannot rescore against different tasks/labels")
    store, results = ReportStore(directory / "reports"), []
    if {r["case_id"] for r in original["results"]} != {c.case_id for c in suite.cases}:
        raise ValueError("Archived suite/case identity mismatch")
    for case in suite.cases:
        row = CaseResult.model_validate_json((directory / (case.case_id + ".json")).read_bytes())
        if row.case_id != case.case_id or row.category != case.category:
            raise ValueError("Archived case identity mismatch")
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
        "audit_status": "RECOMPUTED_FROM_SEALED_EVIDENCE",
        "source": "archived_evidence",
        "independent_experiment": False,
        "original_freeze_valid": original["freeze_valid"],
        "evaluator_version": EVALUATOR_VERSION,
        "model_calls": 0,
        "business_http_requests": 0,
        "suite_sha256": frozen["suite_sha256"],
        "results": results,
    }
