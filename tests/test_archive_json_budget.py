"""Archive metadata has a separate bounded budget; model parsing never inherits it."""

import json

import pytest

from agenttest.eval_v2.archive import MAX_ARCHIVE_BYTES, MAX_ARCHIVE_NODES, read_json
from agenttest.plan_json import bounded_json


def test_repeated_frozen_contexts_can_be_read_but_not_as_model_dsl(tmp_path):
    raw = json.dumps({"contexts": [[0] * 1000 for _ in range(24)]})
    path = tmp_path / "freeze.json"
    path.write_text(raw, encoding="utf-8")
    assert len(read_json(path)["contexts"]) == 24
    with pytest.raises(ValueError, match="structure"):
        bounded_json(raw, 5_000_000)


@pytest.mark.parametrize("raw", [
    '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}',
    "[" * 42 + "0" + "]" * 42,
])
def test_archive_still_rejects_duplicates_nonfinite_and_deep_values(tmp_path, raw):
    path = tmp_path / "invalid.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(path)


def test_archive_node_limit_is_still_enforced(tmp_path):
    path = tmp_path / "oversized-tree.json"
    path.write_text(json.dumps([0] * MAX_ARCHIVE_NODES), encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(path)


def test_archive_byte_budget_still_rejects_a_large_scalar(tmp_path):
    path = tmp_path / "oversized-bytes.json"
    path.write_bytes(b'"' + b"a" * MAX_ARCHIVE_BYTES + b'"')
    with pytest.raises(ValueError):
        read_json(path)
