"""Owner and transition boundaries survive journal transaction consolidation."""

import pytest

from agenttest.task_store import TaskConflict, TaskStore


def test_journal_operations_keep_owner_isolation_and_atomic_transitions(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.create("task", "owner", "submission", "project", "identity", "request", "QUEUED", {})
    store.save_context("task", "owner", {"text": "original"})
    store.event("task", "QUEUED")
    with pytest.raises(KeyError):
        store.save_context("task", "other", {"text": "replaced"})
    with pytest.raises(KeyError):
        store.context("task", "other")
    with pytest.raises(KeyError):
        store.events("task", "other")
    with pytest.raises(KeyError):
        store.save_test("run", "task", "other", "project", "identity", "contract")
    with pytest.raises(TaskConflict):
        store.transition("task", "other", "QUEUED", "RUNNING")
    row = store.transition("task", "owner", "QUEUED", "RUNNING", summary={"ready": True})
    assert row["status"] == "RUNNING" and row["summary"] == {"ready": True}
    with pytest.raises(TaskConflict):
        store.transition("task", "owner", "QUEUED", "FINISHED")
    assert store.context("task", "owner") == {"text": "original"}
    assert len(store.events("task", "owner")) == 1
    assert store.saved_tests("other") == []
