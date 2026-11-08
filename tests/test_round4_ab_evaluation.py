"""Small pre-paid checks for the fourth-round fixture; no model, network or paid API."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts/round4_ab") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts/round4_ab"))

import fixture  # noqa: E402

PLAN = ROOT / "docs/mixed-planning-round4-ab-plan-20261108.md"


def test_proposals_are_the_frozen_plan_rows():
    text = PLAN.read_text(encoding="utf-8")
    items = fixture.tasks(PLAN)
    assert [item["id"] for item in items] == ["F1", "F2", "F3", "F4"]
    for item in items:
        assert re.search(r"^\| " + item["id"] + r" \| " + re.escape(item["request"]) + r" \|$",
                         text, re.M), item["id"]
        assert item["category"] == item["id"]
        assert item["config"] in fixture.configs()


def test_rubric_matches_the_reference_behaviour():
    items = {item["id"]: item for item in fixture.tasks(PLAN)}
    assert [step["operation"] for step in items["F1"]["rubric"]["steps"]] == [
        "listShelf", "showCard", "checkPulse"]
    assert items["F1"]["rubric"]["order"] == [0, 1, 2]
    assert items["F1"]["rubric"]["bindings"][0]["selector"] == "$.cards[0].ref"
    assert [gap["operation"] for gap in items["F2"]["rubric"]["gaps"]] == [
        "readGate", "readKeeper"]
    assert all(gap["kind"] == "permission" for gap in items["F2"]["rubric"]["gaps"])
    assert [step["operation"] for step in items["F3"]["rubric"]["steps"]] == [
        "checkPulse", "checkPulse", "readLedger"]
    assert items["F3"]["rubric"]["order"] == [0, 1, 2]
    content = items["F4"]["rubric"]["gaps"][0]
    assert (content["kind"], content["operation"], content["selector"]) == (
        "content", "showCard", "$.title")
    assert items["F4"]["rubric"]["bindings"][0]["selector"] == "$.cards[0].slip"


def test_routing_separates_identities_and_missing_resources():
    for path, role, status in [
        ("/shelves/12", "custodian", 200),
        ("/shelves/12", None, 401),
        ("/shelves/12", "curator", 403),
        ("/shelves/13", "custodian", 404),
        ("/keepers/9", "auditor", 200),
        ("/keepers/9", "inspector", 403),
        ("/slips/S-31", "custodian", 200),
        ("/pulse", None, 200),
        ("/pulse", "inspector", 403),
        ("/unknown", None, 404),
    ]:
        _, code, _, _, _ = fixture.resolve(path, {}, role)
        assert code == status, (path, role, code)


def test_every_project_stays_read_only_and_approved():
    for key, cfg in fixture.configs().items():
        generic = cfg["generic"]
        assert generic["allow_mutations"] is False and generic["max_requests"] == 8
        assert generic["isolated_confirmed"] is True
        assert generic["base_url"] == f"http://127.0.0.1:{fixture.PORT}"
        identifiers = {op["operation_id"] for op in generic["operations"]}
        assert all(op["role"] in {None, *fixture.ALIASES} for op in generic["operations"])
        for op in generic["operations"]:
            for argument in op["arguments"]:
                if argument.get("source_operation"):
                    assert argument["source_operation"] in identifiers, (key, argument)
            assert op["enabled"] is True
