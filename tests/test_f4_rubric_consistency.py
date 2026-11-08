"""Pre-paid checks for the requirement-to-rubric lint and the F4 recheck fixture."""

import copy
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/f4-recheck-plan-20261108.md"
EVIDENCE = ROOT / "docs/evidence/mixed-planning-round4-ab-20261108"


def load(name, path):
    """Load an evaluation module under a unique name; the tests never touch sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


consistency = load("f4_recheck_consistency", ROOT / "scripts/f4_recheck/consistency.py")
fixture = load("f4_recheck_fixture", ROOT / "scripts/f4_recheck/fixture.py")


def kinds(text, rubric, config):
    return [
        row["kind"]
        for row in consistency.lint(text, rubric, fixture.scope(config), fixture.ALIASES)
    ]


def proposals():
    return {task["id"]: task for task in fixture.tasks(PLAN)}


def test_proposals_are_the_frozen_plan_rows():
    text = PLAN.read_text(encoding="utf-8")
    items = proposals()
    assert list(items) == ["G1", "G2", "G3"]
    for item in items.values():
        pattern = r"^\| " + item["id"] + r" \| " + re.escape(item["request"]) + r" \|$"
        assert re.search(pattern, text, re.M), item["id"]


def test_three_new_rubrics_are_consistent():
    for item in proposals().values():
        assert kinds(item["request"], item["rubric"], item["config"]) == [], item["id"]


def test_extra_action_is_detected():
    item = copy.deepcopy(proposals()["G1"])
    item["rubric"]["steps"].append(
        dict(operation="readVoucher", role="clerk", values={}, status=200, fields=[])
    )
    assert "extra_action" in kinds(item["request"], item["rubric"], item["config"])


def test_missing_action_is_detected():
    item = copy.deepcopy(proposals()["G1"])
    item["rubric"]["steps"] = item["rubric"]["steps"][:2]
    item["rubric"]["order"] = [0, 1]
    assert "missing_action" in kinds(item["request"], item["rubric"], item["config"])


def test_wrong_identity_is_detected():
    item = copy.deepcopy(proposals()["G1"])
    item["rubric"]["steps"][1]["role"] = "clerk"
    found = kinds(item["request"], item["rubric"], item["config"])
    assert "wrong_identity" in found and "role_not_approved" in found


def test_wrong_order_is_detected():
    item = copy.deepcopy(proposals()["G1"])
    item["rubric"]["order"] = [1, 0, 2]
    assert "wrong_order" in kinds(item["request"], item["rubric"], item["config"])


def test_unjustified_repeat_is_detected():
    item = copy.deepcopy(proposals()["G1"])
    item["rubric"]["steps"].append(copy.deepcopy(item["rubric"]["steps"][2]))
    item["rubric"]["order"] = [0, 1, 2, 3]
    assert "unjustified_repeat" in kinds(item["request"], item["rubric"], item["config"])


def test_approved_assertion_cannot_be_held():
    item = copy.deepcopy(proposals()["G1"])
    item["rubric"]["gaps"] = [
        dict(
            kind="content",
            operation="readEntry",
            role="registrar",
            selector="$.label",
            operator="equals",
            value="draft",
            source="$.label",
        )
    ]
    found = kinds(item["request"], item["rubric"], item["config"])
    assert "wrong_hold" in found


def test_permission_gap_on_its_own_role_is_rejected():
    item = copy.deepcopy(proposals()["G3"])
    item["rubric"]["gaps"][0]["role"] = "warden"
    assert "wrong_hold" in kinds(item["request"], item["rubric"], item["config"])


def test_unapproved_assertion_cannot_be_dropped():
    item = copy.deepcopy(proposals()["G2"])
    item["rubric"]["gaps"] = []
    assert "missing_hold" in kinds(item["request"], item["rubric"], item["config"])


def test_frozen_round_four_f4_is_reported_as_inconsistent():
    """The known round-four defect must be caught before any paid position."""
    freeze = json.loads((EVIDENCE / "freeze.json").read_bytes())
    task = next(item for item in freeze["tasks"] if item["id"] == "F4")
    module = Path("scripts/round4_ab/fixture.py")
    namespace = {}
    exec(compile(module.read_text(encoding="utf-8"), str(module), "exec"), namespace)
    facts, alerts = namespace["FACTS"], namespace["ALIASES"]
    catalog = namespace["configs"]()[task["config"]]
    project = {
        op["operation_id"]: dict(
            role=op["role"],
            labels=facts[op["operation_id"]][1],
            checks=[(c["select"], c["operator"], c["expected"]) for c in op["checks"]],
        )
        for op in catalog["generic"]["operations"]
    }
    findings = consistency.lint(task["request"], task["rubric"], project, alerts)
    assert [row["kind"] for row in findings] == ["extra_action"], findings
    assert "fetchNotice" in findings[0]["detail"]
