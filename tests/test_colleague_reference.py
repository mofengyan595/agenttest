import copy
import importlib.util
import json
from pathlib import Path

import pytest
from test_colleague_targets import targets

ROOT = Path.cwd()
SPEC = importlib.util.spec_from_file_location("reference_v16_draft",
    ROOT / "scripts/check_colleague_reference.py")
reference = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reference)
DOCUMENTS = {kind: targets.document(kind) for kind in ("parcels", "reservations", "directory")}


def corrected_fixture():
    manifest = json.loads((ROOT / "docs/testing-colleague-acceptance-v15.json").read_bytes())
    manifest["require_literal_preflight"] = True
    manifest["tasks"][0]["literal_checks"][0]["value"] = "jasper_units_1609"
    return manifest


def test_old_frozen_reference_remains_unchanged_and_new_gate_catches_stale_literal():
    path = ROOT / "docs/testing-colleague-acceptance-v15.json"
    original = path.read_bytes()
    manifest = json.loads(original)
    legacy = reference.check_reference(manifest, DOCUMENTS)
    assert legacy["model_calls"] == legacy["target_http_requests"] == 0
    manifest["require_literal_preflight"] = True
    with pytest.raises(ValueError, match="REFERENCE_LITERAL_PRECONDITION_MISMATCH"):
        reference.check_reference(manifest, DOCUMENTS)
    assert path.read_bytes() == original


def test_matching_declared_literals_and_choice_pass_without_model_or_http():
    result = reference.check_reference(corrected_fixture(), DOCUMENTS)
    assert [p["checked_literal_assertions"] for p in result["projects"]] == [1, 0, 4]
    assert sum(p["checked_fields"] for p in result["projects"]) == 17
    assert result["model_calls"] == result["target_http_requests"] == 0


def test_valid_but_unrequested_literal_is_not_a_fresh_reference():
    manifest = corrected_fixture()
    manifest["tasks"][0]["request"] = manifest["tasks"][0]["request"].replace(
        "jasper_units_1609", "a_new_operator_value")
    with pytest.raises(ValueError, match="REFERENCE_LITERAL_SOURCE_MISMATCH"):
        reference.check_reference(manifest, DOCUMENTS)


def test_initial_body_and_choice_cannot_drift_apart():
    manifest = corrected_fixture()
    manifest["tasks"][1]["initial_body"]["title"] = "stale_title"
    with pytest.raises(ValueError, match="REFERENCE_INPUT_CHOICE_MISMATCH"):
        reference.check_reference(manifest, DOCUMENTS)


def test_literal_preconditions_preserve_json_scalar_type():
    task = copy.deepcopy(corrected_fixture()["tasks"][2])
    task["literal_checks"][0]["value"] = 16.0
    with pytest.raises(ValueError, match="REFERENCE_LITERAL_PRECONDITION_MISMATCH"):
        reference.check_literal_preconditions(task)
