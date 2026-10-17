import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agenttest.pocketbase_acceptance import schedule, summarize
from agenttest.pocketbase_live import executable_bytes, execute, stop, unattempted


@pytest.mark.asyncio
async def test_missing_operator_approval_refuses_before_reading_files_or_starting_runtime(tmp_path):
    with pytest.raises(ValueError, match="approval is required"):
        await execute(tmp_path / "never-created", Path("missing.zip"), Path("missing.env"))
    assert not (tmp_path / "never-created").exists()


def test_runtime_rejects_replaced_download_even_with_expected_file_size():
    archive = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=12_833_751),
                              read_bytes=lambda: b"replacement executable")
    with pytest.raises(ValueError, match="archive hash changed"):
        executable_bytes(archive)


def test_cleanup_reaps_only_owned_process_and_escalates_once_on_timeout():
    process = Mock()
    process.poll.side_effect = [None, 0]
    process.wait.side_effect = [subprocess.TimeoutExpired("owned process", 8), 0]
    assert stop(process)
    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2


def test_unconfirmed_process_exit_is_not_reported_as_cleaned_up():
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = subprocess.TimeoutExpired("owned process", 5)
    assert not stop(process)
    assert stop(None)


def test_interrupted_trial_keeps_unknown_calls_usage_and_requests_in_denominator():
    rows = [unattempted(slot, "STOPPED") for slot in schedule()]
    for i in range(6):
        rows[i].update(run_id="baseline" + str(i), outcome="PASS")
        rows[i]["grade"].update(verified=True, outcome="PASS", requests=3, task_satisfied=True)
    rows[6].update(outcome="ATTEMPT_OUTCOME_UNKNOWN", model_calls=None)
    rows[6]["grade"].update(outcome="ATTEMPT_OUTCOME_UNKNOWN", requests=None)
    summary = summarize(rows, "real")
    assert summary["unconfirmed_model_call_slots"] == summary["attempted_trials"] == 1
    assert summary["not_attempted"] == 17
    assert summary["real_model_calls"] is summary["total_tokens"] is None
    assert summary["usage_kind"] == "UNKNOWN"
    rows[0]["grade"]["verified"] = False
    with pytest.raises(ValueError, match="six successful baselines"):
        summarize(rows, "real")


@pytest.mark.parametrize("tokens", [True, -1, 1.5])
def test_budget_audit_does_not_accept_boolean_negative_or_fractional_token_counts(tokens):
    rows = [unattempted(slot, "STOPPED") for slot in schedule()]
    rows[0]["total_tokens"] = tokens
    with pytest.raises(ValueError, match="token accounting"):
        summarize(rows, "real")
