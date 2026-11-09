"""An order edge that collects a repeated operation as a set must name its instances."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/rw_lifecycle"))

from agenttest.request_requirements import (  # noqa: E402
    order_ambiguity,
    request_atoms,
)

EVIDENCE = ROOT / "docs/evidence/rw-lifecycle-20261109-v3/result.json"


def historical(case):
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    row = next(item for item in data["results"] if item["case"] == case)
    wire = json.loads(next(item for item in row["focus"] if item.get("content"))["content"])
    requirements = [dict(source_id=source_id, disposition="required",
                         actions=entry.get("actions") or [],
                         before=entry.get("before") or [], same_resource=[],
                         response_checks=[])
                    for source_id, entry in wire["requirement_map"].items()]
    return row["text"], requirements


def test_the_archived_reply_is_diagnosed_as_a_missing_instance_not_a_cycle():
    text, requirements = historical("T1V3")
    ambiguity = order_ambiguity(requirements, request_atoms(text))
    assert ambiguity is not None
    assert ambiguity["clause"].startswith("request_")
    assert significance(ambiguity) >= 4


def significance(ambiguity):
    """How many clause instances one unaddressed side collects."""
    return max(len(ambiguity["earlier"]), len(ambiguity["later"]))


def test_a_clause_level_set_order_without_repetition_is_not_ambiguous():
    text = "创建条目，然后读取条目。"
    atoms = request_atoms(text)
    requirements = [
        dict(source_id="request_1", disposition="required",
             actions=[dict(operation_id="createItem", role="author")], before=[],
             same_resource=[], response_checks=[]),
        dict(source_id="request_2", disposition="required",
             actions=[dict(operation_id="getItem", role="author")],
             before=[dict(earlier="createItem", later="getItem")],
             same_resource=[], response_checks=[]),
    ]
    assert order_ambiguity(requirements, atoms) is None


def test_an_addressed_pair_over_repeated_reads_is_not_ambiguous():
    text = ("先创建条目；按 id 读取一次，确认返回 200；删除该条目；"
            "按同一个 id 再读取一次，确认返回 404。")
    atoms = request_atoms(text)
    requirements = [
        dict(source_id="request_1", disposition="required",
             actions=[dict(operation_id="createItem", role="author")], before=[],
             same_resource=[], response_checks=[]),
        dict(source_id="request_2", disposition="required",
             actions=[dict(operation_id="getItem", role="author")], before=[],
             same_resource=[], response_checks=[]),
        dict(source_id="request_3", disposition="required",
             actions=[dict(operation_id="deleteItem", role="author")],
             before=[dict(earlier="createItem", later="deleteItem")],
             same_resource=[], response_checks=[]),
        dict(source_id="request_4", disposition="required",
             actions=[dict(operation_id="getItem", role="author")],
             before=[dict(earlier="deleteItem", later="getItem",
                          earlier_source_id="request_3", earlier_index=0,
                          later_source_id="request_4", later_index=0)],
             same_resource=[], response_checks=[]),
    ]
    assert order_ambiguity(requirements, atoms) is None
