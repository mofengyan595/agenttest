from pathlib import Path

import pytest

from agenttest.report import ReportStore, RunReport


def report():
    return RunReport(scenario_sha256="test", contract_sha256="test", environment="isolated",
                     target_origin="http://127.0.0.1:8080")


def test_transient_windows_replace_lock_retries_only_publication(tmp_path, monkeypatch):
    replace, calls = Path.replace, []

    def transient(path, target):
        calls.append(path)
        if len(calls) <= 2:
            error = PermissionError("synthetic Windows sharing error")
            error.winerror = 5
            raise error
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", transient)
    monkeypatch.setattr("agenttest.report.time.sleep", lambda delay: None)
    store, value = ReportStore(tmp_path), report()
    store.save(value)
    assert len(calls) == 3 and store.read(value.run_id) == value


@pytest.mark.parametrize("winerror,expected_calls", [(5, 5), (32, 5), (33, 5), (None, 1)])
def test_permanent_or_non_windows_denial_still_fails(tmp_path, monkeypatch,
                                                    winerror, expected_calls):
    calls = []

    def denied(path, target):
        calls.append(path)
        error = PermissionError("permanent denial")
        if winerror is not None:
            error.winerror = winerror
        raise error

    monkeypatch.setattr(Path, "replace", denied)
    monkeypatch.setattr("agenttest.report.time.sleep", lambda delay: None)
    with pytest.raises(PermissionError):
        ReportStore(tmp_path).save(report())
    assert len(calls) == expected_calls
