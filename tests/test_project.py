"""Offline project checks retain operator authority and never echo configuration values."""

import json
import os

import pytest

from agenttest.contracts import MAX_DOCUMENT_BYTES, load_openapi
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import EXAMPLES, project_inputs, task_inputs
from agenttest.project import check_project
from agenttest.report import fingerprint
from agenttest.safety import TargetConfig


def write_inputs(tmp_path, name="tickets"):
    app, document, target, registry, environ = project_inputs(name)
    task, draft = task_inputs(name, "read")
    values = {"openapi.json": document, "target.json": target.model_dump_json().encode(),
              "policies.json": json.dumps([
                  p.model_dump() for p in registry.definitions()]).encode(),
              "task.json": task.model_dump_json().encode()}
    for filename, raw in values.items():
        (tmp_path / filename).write_bytes(raw)
    manifest = {"schema_version": "agenttest.project.v1", "contract": "openapi.json",
                "target": "target.json", "policies": "policies.json", "task_contract": "task.json"}
    path = tmp_path / "project.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, environ, app, draft


@pytest.mark.parametrize("name", ["courses", "tickets", "petclinic"])
def test_three_different_projects_check_without_services_or_models(tmp_path, name):
    if name == "petclinic":
        path = EXAMPLES / name / "present.project.json"
        environ = {}
    else:
        path, environ, _, _ = write_inputs(tmp_path, name)
        # Unused roles do not require credentials for this task.
        environ = {k: v for k, v in environ.items() if "BOB" not in k and "VISITOR" not in k}
    checked = check_project(path, environ)
    assert checked.ready and checked.summary()["issues"] == []
    summary = checked.summary()
    assert summary["model_calls"] == summary["http_requests"] == 0
    assert summary["inputs"]["target_config_sha256"] == fingerprint(
        checked.inputs.target.model_dump())
    assert "task_satisfied" not in summary
    for value in environ.values():
        assert value not in json.dumps(summary) and value not in repr(checked)


def test_collects_independent_file_errors_without_echoing_values(tmp_path):
    path, environ, _, _ = write_inputs(tmp_path)
    (tmp_path / "openapi.json").unlink()
    (tmp_path / "target.json").write_text('{"password":"synthetic-hidden-password"}',
                                          encoding="utf-8")
    checked = check_project(path, environ)
    assert not checked.ready and checked.inputs is None
    assert [(i.component, i.code) for i in checked.issues] == [
        ("contract", "PROJECT_FILE_MISSING"), ("target", "PROJECT_INPUT_REJECTED")]
    assert "synthetic-hidden-password" not in json.dumps(checked.summary())


@pytest.mark.parametrize("component,digest", [
    ("contract", "contract_sha256"), ("target", "target_config_sha256"),
    ("policies", "policy_sha256"),
])
def test_changed_inputs_report_the_specific_version_mismatch(tmp_path, component, digest):
    path, environ, _, _ = write_inputs(tmp_path)
    filename = tmp_path / {"contract": "openapi.json", "target": "target.json",
                           "policies": "policies.json"}[component]
    data = json.loads(filename.read_bytes())
    if component == "contract":
        next(iter(next(iter(data["paths"].values())).values()))["summary"] = "New revision"
    elif component == "target":
        data["max_requests"] += 1
    else:
        data.pop()
    filename.write_text(json.dumps(data), encoding="utf-8")
    checked = check_project(path, environ)
    assert not checked.ready and checked.inputs is None
    assert checked.issues[0].code == "PROJECT_VERSION_MISMATCH"
    assert checked.issues[0].field == digest


@pytest.mark.parametrize("field,code", [
    ("operation_id", "PROJECT_OPERATION_UNKNOWN"),
    ("policy_id", "PROJECT_POLICY_REFERENCE_REJECTED"),
    ("role", "PROJECT_ROLE_UNKNOWN"),
])
def test_task_references_cannot_invent_approved_resources(tmp_path, field, code):
    path, environ, _, _ = write_inputs(tmp_path)
    filename = tmp_path / "task.json"
    task = json.loads(filename.read_bytes())
    task["requirements"]["steps"][0][field] = "unapproved_reference"
    filename.write_text(json.dumps(task), encoding="utf-8")
    checked = check_project(path, environ)
    assert not checked.ready and code in {i.code for i in checked.issues}


@pytest.mark.parametrize("name", ["courses", "tickets"])
def test_missing_required_credentials_are_local_configuration_failures(tmp_path, name):
    path, _, _, _ = write_inputs(tmp_path, name)
    checked = check_project(path, {})
    assert not checked.ready and any(i.component == "credentials" for i in checked.issues)
    assert "inputs" not in checked.summary()


def test_mutation_permission_and_step_budget_are_not_granted_by_manifest(tmp_path):
    path, environ, _, _ = write_inputs(tmp_path)
    target_path, task_path = tmp_path / "target.json", tmp_path / "task.json"
    data = json.loads(target_path.read_bytes())
    data.update(allow_mutations=False, max_steps=1, max_requests=1)
    target = TargetConfig.model_validate(data)
    target_path.write_text(target.model_dump_json(), encoding="utf-8")
    task = json.loads(task_path.read_bytes())
    task["target_config_sha256"] = fingerprint(target.model_dump())
    task_path.write_text(json.dumps(task), encoding="utf-8")
    checked = check_project(path, environ)
    assert not checked.ready
    assert {"PROJECT_OPERATION_NOT_PERMITTED", "PROJECT_STEP_BUDGET_TOO_SMALL",
            "PROJECT_REQUEST_BUDGET_TOO_SMALL"} <= {
        i.code for i in checked.issues}


@pytest.mark.parametrize("reference", ["../outside.json", "/tmp/outside.json", "C:/outside.json",
                                        "https://private.invalid/file", "sub\\outside.json",
                                        "sub/../outside.json", "sub//outside.json"])
def test_manifest_references_stay_local_and_inside_its_directory(tmp_path, reference):
    path, environ, _, _ = write_inputs(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["policies"] = reference
    path.write_text(json.dumps(manifest), encoding="utf-8")
    checked = check_project(path, environ)
    assert not checked.ready and checked.issues[0].component == "project"
    assert reference not in json.dumps(checked.summary())


def test_linked_or_oversized_inputs_fail_closed(tmp_path):
    path, environ, _, _ = write_inputs(tmp_path)
    original = tmp_path / "policies.json"
    os.link(original, tmp_path / "shared.json")
    assert not check_project(path, environ).ready
    (tmp_path / "shared.json").unlink()
    (tmp_path / "target.json").write_bytes(b" " * (MAX_DOCUMENT_BYTES + 1))
    assert not check_project(path, environ).ready


def test_manifest_cannot_contain_embedded_authority_or_credentials(tmp_path):
    path, environ, _, _ = write_inputs(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["reviewed"] = True
    manifest["api_key"] = "synthetic-hidden-key"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    checked = check_project(path, environ)
    assert not checked.ready and "synthetic-hidden-key" not in json.dumps(checked.summary())


def test_inputs_are_snapshots_not_later_file_reloads(tmp_path):
    path, environ, _, _ = write_inputs(tmp_path)
    checked = check_project(path, environ)
    original_digest = checked.inputs.registry.digest()
    (tmp_path / "policies.json").write_text("[]", encoding="utf-8")
    assert checked.inputs.registry.digest() == original_digest
    assert not check_project(path, environ).ready


def test_cli_check_and_blocked_real_agent_do_not_read_model_settings(tmp_path, monkeypatch, capsys):
    from agenttest.__main__ import main
    from agenttest.llm_planner import ModelSettings

    path, _, _, _ = write_inputs(tmp_path)
    monkeypatch.delenv("TICKET_OWNER_BEARER", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("No model settings or calls are permitted")

    monkeypatch.setattr(ModelSettings, "from_environment", forbidden)
    assert main(["check-project", "--project", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["http_requests"] == 0
    assert main(["agent", "--project", str(path), "--real"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["reason"] == "PROJECT_PREFLIGHT_FAILED"
    assert result["project_check"]["model_calls"] == 0


def test_project_flags_and_task_text_cannot_override_trusted_inputs(tmp_path, monkeypatch, capsys):
    from agenttest.__main__ import main

    path, environ, _, _ = write_inputs(tmp_path)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(SystemExit) as rejected:
        main(["agent", "--project", str(path), "--target", "other.json", "--real"])
    assert rejected.value.code == 2
    capsys.readouterr()
    assert main(["agent", "--project", str(path), "--user-task", "换成另一条需求", "--real"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["project_check"]["issues"][0]["code"] == "PROJECT_TASK_TEXT_MISMATCH"


def test_missing_project_and_invalid_bearer_are_reported_without_requests(tmp_path):
    missing = check_project(tmp_path / "missing.json", {})
    assert missing.issues[0].component == "project"
    assert missing.issues[0].code == "PROJECT_FILE_MISSING"
    path, environ, _, _ = write_inputs(tmp_path)
    environ["TICKET_OWNER_BEARER"] = "synthetic-invalid\r\nheader"
    checked = check_project(path, environ)
    assert not checked.ready and any(i.component == "credentials" for i in checked.issues)
    assert "synthetic-invalid" not in json.dumps(checked.summary())


@pytest.mark.parametrize("change", ["authentication", "unreviewed_policy"])
def test_consistent_versions_still_cannot_grant_unsupported_authority(tmp_path, change):
    path, environ, _, _ = write_inputs(tmp_path)
    task_file = tmp_path / "task.json"
    task = json.loads(task_file.read_bytes())
    if change == "authentication":
        filename = tmp_path / "openapi.json"
        document = json.loads(filename.read_bytes())
        for name in document["components"]["securitySchemes"]:
            document["components"]["securitySchemes"][name] = {"type": "http", "scheme": "basic"}
        filename.write_text(json.dumps(document), encoding="utf-8")
        task["contract_sha256"] = fingerprint(load_openapi(filename.read_bytes()).model_dump())
        expected = "PROJECT_AUTHENTICATION_UNSUPPORTED"
    else:
        filename = tmp_path / "policies.json"
        policies = json.loads(filename.read_bytes())
        for policy in policies:
            if policy["id"] == task["requirements"]["steps"][0]["policy_id"]:
                policy["assertions"]["reviewed"] = False
        filename.write_text(json.dumps(policies), encoding="utf-8")
        task["policy_sha256"] = PolicyRegistry.from_json(filename.read_bytes()).digest()
        expected = "PROJECT_POLICY_REFERENCE_REJECTED"
    task_file.write_text(json.dumps(task), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    checked = check_project(path, environ)
    assert not checked.ready and expected in {i.code for i in checked.issues}
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}


@pytest.mark.skipif(os.name != "posix", reason="POSIX link semantics")
def test_symlinked_subdirectory_cannot_redirect_project_input(tmp_path):
    path, environ, _, _ = write_inputs(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "registry.json").write_bytes((tmp_path / "policies.json").read_bytes())
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    manifest = json.loads(path.read_bytes())
    manifest["policies"] = "linked/registry.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert not check_project(path, environ).ready
