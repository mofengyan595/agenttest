"""Preflight and usage audit do not turn old tasks or unknown usage into new success."""

import importlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from agenttest.report import ModelCallStats
from agenttest.task_store import TaskStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def acceptance(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("run_reliable_acceptance")


def test_frozen_requests_match_documented_inputs_before_any_model_call(acceptance):
    manifest = json.loads((ROOT / "docs/reliable-colleague-acceptance-v1.json").read_bytes())
    original = deepcopy(manifest)
    checked = acceptance.preflight(manifest)
    assert checked["model_calls"] == checked["target_requests"] == 0
    assert [t["operations"] for t in checked["tasks"]] == [6, 5, 48]
    assert manifest == original
    manifest["tasks"][0]["initial_body"]["quantity"] = 4
    with pytest.raises(ValueError):
        acceptance.preflight(manifest)


def test_historical_request_cannot_be_relabelled_new(acceptance):
    manifest = json.loads((ROOT / "docs/reliable-colleague-acceptance-v1.json").read_bytes())
    old = json.loads((ROOT / "docs/testing-colleague-acceptance-v16.json").read_bytes())
    manifest["tasks"][0]["request"] = old["tasks"][0]["request"]
    with pytest.raises(ValueError, match="HISTORICAL_REQUEST"):
        acceptance.preflight(manifest)


def test_unknown_usage_audit_never_reconciles_or_replaces_tokens(acceptance, tmp_path):
    path = tmp_path / "tasks.sqlite3"
    db = TaskStore(path)
    call, task = uuid4().hex, uuid4().hex
    db.begin_model_call(call, task)
    db.finish_model_call(call, ModelCallStats(calls=1, total_tokens=None))
    result = acceptance.usage(path)
    assert result["usage_known"] is False and result["total_tokens"] is None
    assert result["model_calls"] == 1 and result["calls"][0]["stats"]["total_tokens"] is None
    assert db.model_usage_blocked()
    assert acceptance.usage(tmp_path / "missing.sqlite3")["model_calls"] is None
