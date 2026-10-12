import json

import pytest
from fastapi.testclient import TestClient

from agenttest.main import create_app
from agenttest.report import (
    MAX_REPORT_BYTES,
    CheckResult,
    Outcome,
    ReportStore,
    RunReport,
    StepResult,
)
from agenttest.report_queries import public_report


def report(store, **kwargs):
    row = RunReport(scenario_sha256="a", contract_sha256="b", environment="mini",
                    target_origin="http://127.0.0.1", **kwargs)
    store.save(row)
    return row


def test_catalog_pagination_filter_order_and_validation(tmp_path):
    store = ReportStore(tmp_path)
    first = report(store, started_at="2026-10-11T10:00:00+00:00", outcome=Outcome.PASS)
    second = report(store, started_at="2026-10-12T10:00:00+00:00",
                    outcome=Outcome.ASSERTION_FAILED, workflow_completed=False)
    client = TestClient(create_app(tmp_path))
    page = client.get("/api/runs?limit=1").json()
    assert page["total"] == 2 and page["items"][0]["run_id"] == second.run_id
    assert page["items"][0]["workflow_completed"] is False
    assert client.get("/api/runs?limit=1&offset=1").json()["items"][0]["run_id"] == first.run_id
    filtered = client.get("/api/runs?outcome=PASS&order=asc").json()
    assert filtered["total"] == 1 and filtered["items"][0]["run_id"] == first.run_id
    assert set(filtered["items"][0]) == {
        "run_id", "started_at", "environment", "outcome", "steps", "workflow_completed"}
    for query in ("limit=0", "limit=101", "offset=-1", "offset=2001", "outcome=BUG", "order=x"):
        assert client.get("/api/runs?" + query).status_code == 422


def test_empty_missing_directory_and_invalid_reports(tmp_path):
    client = TestClient(create_app(tmp_path / "missing"))
    assert client.get("/api/runs").json()["total"] == 0
    root = tmp_path / "missing"
    store = ReportStore(root)
    valid = report(store)
    path = store.root / valid.run_id / "report.json"
    path.write_text('{"password":"never-echo-this"}', encoding="utf-8")
    page = client.get("/api/runs").json()
    assert page["total"] == 0 and page["skipped"] == 1
    response = client.get(f"/api/runs/{valid.run_id}")
    assert response.status_code == 404 and "never-echo-this" not in response.text
    assert client.get("/api/runs/" + valid.run_id.upper()).status_code == 404


def test_legacy_fields_remain_absent_and_checks_are_redacted(tmp_path):
    store = ReportStore(tmp_path)
    row = report(store)
    data = row.model_dump()
    for key in ("dsl_valid", "policy_authorized", "intent_verified", "task_satisfied",
                "task_mode", "workflow_completed"):
        data.pop(key)
    data["unknown_secret"] = "secret-extra"
    (store.root / row.run_id / "report.json").write_text(json.dumps(data), encoding="utf-8")
    response = TestClient(create_app(tmp_path)).get(f"/api/runs/{row.run_id}")
    assert response.status_code == 200
    for key in ("dsl_valid", "policy_authorized", "intent_verified", "task_satisfied", "task_mode"):
        assert key not in response.json()
    assert "secret-extra" not in response.text
    row.steps = [StepResult(step_id="x", operation_id="read", method="GET", path_template="/x",
                            outcome=Outcome.PASS, reason="OK", policy_sha256="a",
                            checks=[CheckResult(kind="field", passed=True, expected="secret",
                                                actual={"token": "hidden"})])]
    store.save(row)
    payload = TestClient(create_app(tmp_path)).get(f"/api/runs/{row.run_id}").json()
    assert payload["steps"][0]["checks"][0]["expected"] == "[REDACTED]"
    assert payload["steps"][0]["checks"][0]["actual"] == "[REDACTED]"
    assert public_report(row).steps[0].checks[0].actual == "[REDACTED]"
    assert row.steps[0].checks[0].expected == "secret"


def test_missing_legacy_metadata_is_not_fabricated(tmp_path):
    store = ReportStore(tmp_path)
    row = report(store)
    data = row.model_dump()
    for field in ("started_at", "steps", "outcome"):
        data.pop(field)
    (store.root / row.run_id / "report.json").write_text(json.dumps(data), encoding="utf-8")
    client = TestClient(create_app(tmp_path))
    metadata = client.get("/api/runs").json()["items"][0]
    assert metadata["started_at"] is metadata["steps"] is metadata["outcome"] is None
    assert client.get("/api/runs?outcome=NEEDS_REVIEW").json()["total"] == 0


def test_only_approved_http_code_arrays_remain_public(tmp_path):
    store = ReportStore(tmp_path)
    row = report(store)
    row.steps = [StepResult(step_id="x", operation_id="read", method="GET", path_template="/x",
                            outcome=Outcome.PASS, reason="OK", policy_sha256="a", checks=[
                                CheckResult(kind="http_status", passed=True,
                                            expected=[200, 201], actual=200),
                                CheckResult(kind="http_status", passed=False,
                                            expected=["secret"], actual="secret"),
                            ])]
    public = public_report(row)
    assert public.steps[0].checks[0].expected == [200, 201]
    assert public.steps[0].checks[1].expected == "[REDACTED]"


def test_identity_size_and_catalog_budgets(tmp_path, monkeypatch):
    store = ReportStore(tmp_path)
    row = report(store)
    path = store.root / row.run_id / "report.json"
    data = row.model_dump()
    data["run_id"] = "a" * 32
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        store.read(row.run_id)
    path.write_bytes(b" " * (MAX_REPORT_BYTES + 1))
    with pytest.raises(ValueError, match="size"):
        store.read(row.run_id)
    # Damaged current snapshots are not silently overwritten by the CAS publisher.
    with pytest.raises(ValueError):
        store.save(row)
    path.write_text(row.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr("agenttest.report_queries.MAX_CATALOG_BYTES", 1)
    assert TestClient(create_app(tmp_path)).get("/api/runs").status_code == 503
    monkeypatch.setattr("agenttest.report_queries.MAX_CATALOG_BYTES", 32_000_000)
    monkeypatch.setattr("agenttest.report_queries.MAX_CATALOG_ENTRIES", 0)
    assert TestClient(create_app(tmp_path)).get("/api/runs").status_code == 503


@pytest.mark.parametrize("link_kind", ["directory", "file"])
def test_link_escape_rejected(tmp_path, link_kind):
    outside = ReportStore(tmp_path / "outside")
    row = report(outside)
    store = ReportStore(tmp_path / "catalog")
    store.root.mkdir()
    try:
        if link_kind == "directory":
            (store.root / row.run_id).symlink_to(
                outside.root / row.run_id, target_is_directory=True)
        else:
            (store.root / row.run_id).mkdir()
            (store.root / row.run_id / "report.json").symlink_to(
                outside.root / row.run_id / "report.json")
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(ValueError, match="Linked"):
        store.read(row.run_id)
    page = TestClient(create_app(store.root)).get("/api/runs").json()
    assert page["total"] == 0 and page["skipped"] == 1


def test_no_web_execution_or_arbitrary_file_api(tmp_path):
    client = TestClient(create_app(tmp_path))
    for endpoint in ("/api/run", "/api/execute", "/api/showcase", "/api/plan"):
        assert client.post(endpoint, json={"url": "http://example.com"}).status_code == 404
    assert client.get("/api/runs/.env").status_code == 404
    response = client.get("/health", headers={"Origin": "https://example.com"})
    assert "access-control-allow-origin" not in response.headers
