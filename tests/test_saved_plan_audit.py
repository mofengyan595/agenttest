"""Cold audit checks saved evidence identity and reports observable differences only."""

import runpy
from pathlib import Path

import pytest

from agenttest.eval_v2.archive import seal
from agenttest.evaluation import save_evaluation

audit = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                          "scripts/audit_entry_plans.py"))["audit"]


def test_cold_audit_reports_extra_dependency_and_order_without_rewriting_original(tmp_path):
    expected = [dict(key="p", operation_id="make", role=None, policy_id="make_v1",
                     arguments=[], before=["read"]),
        dict(key="read", operation_id="read", role=None, policy_id="read_v1", before=[],
             arguments=[dict(location="query", name="id", source="p", selector="$.id")])]
    actual = [dict(step_id="consumer", operation_id="read", role=None, policy_id="read_v1",
                   dependencies={"query.id": "invented"}, selectors={"query.id": "$.id"}),
              dict(step_id="producer", operation_id="make", role=None, policy_id="make_v1",
                   dependencies={}, selectors={}),
              dict(step_id="extra", operation_id="list", role=None, policy_id="list_v1",
                   dependencies={}, selectors={})]
    folder = tmp_path / "position-001"
    save_evaluation(dict(cases=[dict(case_id="group", run_id="run", reason=None,
        requested_goals=["read"], required_plan=expected)]), folder / "product.json")
    save_evaluation(dict(run=dict(steps=actual)), folder / "plans.json")
    report = folder / "reports/run/report.json"
    save_evaluation(dict(diagnostic=None), report)
    for name in ("freeze.json", "results.json"):
        save_evaluation({}, tmp_path / name)
    (tmp_path / "summary.md").write_text("synthetic audit fixture", encoding="utf-8")
    seal(tmp_path)
    result = audit(tmp_path)
    assert result["original_archive_verified"] and result["real_model_calls"] == 0
    assert result["runs_with_extra_steps"] == result["runs_with_wrong_dependencies"] == 1
    assert result["runs_with_wrong_order"] == 1 and result["runs_with_missing_steps"] == 0
    report.write_text('{"diagnostic":"changed"}', encoding="utf-8")
    with pytest.raises(ValueError, match="ORIGINAL_ARCHIVE_CHANGED"):
        audit(tmp_path)
