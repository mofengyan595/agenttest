"""Contract facts assist review without granting authority or inventing expectations."""

import copy
import json

import pytest
from pydantic import ValidationError
from test_project import write_inputs

from agenttest.contracts import load_openapi
from agenttest.onboarding import TaskDraft, prepare_project, prepare_task, write_draft
from agenttest.policies import PolicyRegistry
from agenttest.portability_demo import EXAMPLES
from agenttest.project import ProjectConfig, check_project
from agenttest.task_contract import TaskContract


@pytest.mark.parametrize("domain", ["courses", "tickets"])
def test_contract_draft_extracts_facts_but_never_invents_roles_or_approvals(tmp_path, domain):
    contract = load_openapi((EXAMPLES / domain / "openapi.json").read_bytes())
    original = contract.model_dump()
    draft = prepare_project(contract)
    assert len(draft.operations) == len(contract.operations)
    assert {slot.scope for slot in draft.missing} == {"project", "task"}
    assert draft.review_status == "NEEDS_REVIEW" and draft.execution_authorized is False
    assert draft.discovery_summary["http_requests"] == draft.discovery_summary["model_calls"] == 0
    assert all("roles" not in operation and "assertions" not in operation
               for operation in draft.operations)
    assert contract.model_dump() == original
    output = tmp_path / "project-draft.json"
    write_draft(draft, output)
    assert not check_project(output, {}).ready
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate_json(output.read_bytes())
    with pytest.raises(ValueError):
        PolicyRegistry.from_json(output.read_bytes())
    with pytest.raises(FileExistsError):
        write_draft(draft, output)


@pytest.mark.parametrize("domain", ["courses", "tickets"])
def test_followup_draft_computes_reusable_hashes_and_keeps_requirements_unknown(tmp_path, domain):
    path, environ, _, _ = write_inputs(tmp_path, domain)
    source = {name: (tmp_path / name).read_bytes()
              for name in ("openapi.json", "target.json", "policies.json", "task.json")}
    checked = check_project(path, environ)
    draft = prepare_task(path, "新任务：核对一个合成资源的一致性。", environ)
    assert draft.requirements is None and draft.execution_authorized is False
    assert draft.available_policies
    for key in ("contract_sha256", "target_config_sha256", "policy_sha256"):
        assert getattr(draft, key) == checked.summary()["inputs"][key]
    assert len(draft.missing) == 3 and all(slot.scope == "task" for slot in draft.missing)
    assert source == {name: (tmp_path / name).read_bytes() for name in source}
    with pytest.raises(ValidationError):
        TaskContract.model_validate_json(draft.model_dump_json())
    for key, value in environ.items():
        if not key.endswith("_USERNAME"):
            assert value not in draft.model_dump_json()
        assert key not in draft.model_dump_json()


def test_contract_annotations_and_default_or_enum_secrets_are_not_exported():
    data = json.loads((EXAMPLES / "tickets/openapi.json").read_bytes())
    data["info"]["description"] = "untrusted-private-marker"
    for path in data["paths"].values():
        for operation in path.values():
            if not isinstance(operation, dict):
                continue
            operation["description"] = "untrusted-private-marker"
    contract = load_openapi(json.dumps(data))
    draft = prepare_project(contract)
    assert "untrusted-private-marker" not in draft.model_dump_json()
    from agenttest.onboarding import schema_facts

    assert schema_facts({"type": "string", "description": "private-marker",
        "default": "private-marker", "examples": ["private-marker"],
        "enum": ["private-marker", "other-marker"]}) == {"type": "string", "enum_count": 2}


def test_draft_cannot_claim_approval_and_known_credential_text_is_rejected(tmp_path):
    path, environ, _, _ = write_inputs(tmp_path, "tickets")
    credential = environ["TICKET_OWNER_BEARER"]
    with pytest.raises(ValueError):
        prepare_task(path, "核对资源 " + credential, environ)
    data = prepare_task(path, "核对资源。", environ).model_dump(mode="json")
    data["execution_authorized"] = True
    with pytest.raises(ValidationError):
        TaskDraft.model_validate(copy.deepcopy(data))


def test_cli_preparation_does_not_activate_the_draft(tmp_path, capsys):
    from agenttest.__main__ import main

    destination = tmp_path / "review.json"
    assert main(["prepare-project", "--contract", str(EXAMPLES / "tickets/openapi.json"),
                 "--output", str(destination)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["execution_authorized"] is False
    assert main(["check-project", "--project", str(destination)]) == 2
    assert json.loads(capsys.readouterr().out)["configuration_ready"] is False
