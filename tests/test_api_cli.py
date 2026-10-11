import json
from pathlib import Path

from fastapi.testclient import TestClient

from agenttest.__main__ import main
from agenttest.contracts import MAX_DOCUMENT_BYTES
from agenttest.main import create_app


def test_contract_raw_yaml_and_stream_size_limit(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    raw = Path("tests/fixtures/contract.yaml").read_bytes()
    assert client.post("/api/spec/summary", content=raw,
                       headers={"Content-Type": "application/yaml"}).status_code == 200
    response = client.post("/api/spec/summary", content=b" " * (MAX_DOCUMENT_BYTES + 1),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert client.post("/api/spec/summary", content=b"bad",
                       headers={"Content-Type": "application/octet-stream"}).status_code == 415


def test_invalid_input_never_echoes_secrets(tmp_path: Path) -> None:
    response = TestClient(create_app(tmp_path)).post(
        "/api/spec/summary", content=b"password: !!python/object:secret-password {}",
        headers={"Content-Type": "application/yaml"},
    )
    assert response.status_code == 422
    assert "secret-password" not in response.text


def test_cli_demo_report_export_and_api(tmp_path: Path, capsys) -> None:
    assert main(["demo", "--runs-dir", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    run_id = output["run_id"]
    assert output["outcome"] == "PASS" and output["steps"] == 8
    client = TestClient(create_app(tmp_path))
    assert client.get(f"/api/runs/{run_id}").json()["outcome"] == "PASS"
    assert client.get("/api/runs/not-a-uuid").status_code == 404
    exported = tmp_path / "export.json"
    assert main(["report", run_id, "--runs-dir", str(tmp_path), "--output", str(exported)]) == 0
    assert json.loads(exported.read_bytes())["run_id"] == run_id


def test_cli_fault_and_bad_target(tmp_path: Path, capsys) -> None:
    assert main(["demo", "--fault", "ownership", "--runs-dir", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out)["outcome"] == "ASSERTION_FAILED"
    target = tmp_path / "target.json"
    target.write_text('{"base_url":"http://evil.test","allowed_operations":["x"]}')
    assert main(["run", "--target", str(target), "--contract", "missing.json",
                 "--plan", "missing.json", "--runs-dir", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["outcome"] == "TOOL_ERROR"
