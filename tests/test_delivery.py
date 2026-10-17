"""The delivery wrapper must retain incomplete checks and trust existing verdicts only."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from agenttest import delivery
from agenttest.delivery import execute_child


def child_summary(check):
    result = dict(check.expected)
    if check.name == "showcase":
        result["cases"] = [{"case": name, "verified": True} for name in (
            "normal", "fault", "investigation", "java-mock", "mcp")]
    return result


@pytest.fixture
def wrapper(tmp_path, monkeypatch):
    output = tmp_path / "acceptance"
    checks = delivery.delivery_checks(output)
    seen = []
    monkeypatch.setattr(delivery, "private_output", lambda *args: None)
    monkeypatch.setattr(delivery, "checkout_identity", lambda root: {"commit": "fixed"})

    def run(arguments, environ, directory):
        check = next(c for c in checks if c.arguments == arguments)
        seen.append(check.name)
        assert environ["PYTEST_ADDOPTS"] == ""
        assert environ["DEMO_ALICE_USERNAME"] == "alice"
        # Log folders cannot pre-create the target directory required to be new by child checks.
        if check.name in {"portability", "json-literals", "fixed-regressions"}:
            assert not (output / check.name).exists()
        return SimpleNamespace(returncode=0, stdout=json.dumps(child_summary(check)), stderr="")

    monkeypatch.setattr(delivery, "execute_child", run)
    return output, checks, seen


def test_complete_delivery_retains_nine_distinct_checks_and_simulated_scope(wrapper):
    output, checks, seen = wrapper
    result = delivery.check_delivery(output)
    assert result["verified"] and result["verified_checks"] == result["scheduled_checks"] == 9
    assert seen == [c.name for c in checks]
    assert result["real_model_calls"] == result["real_java_requests"] == 0
    assert result["formal_eval_v2_score"] is None and result["model_usage"] == "FAKE_OR_SIMULATED"
    assert json.loads((output / "results.json").read_bytes()) == result
    assert len(list((output / "logs").glob("*/stdout.txt"))) == 9
    with pytest.raises(FileExistsError):
        delivery.check_delivery(output)
    assert len(seen) == 9


@pytest.mark.parametrize("change", ["nonzero_exit", "invalid_json", "wrong_scope", "partial_demo"])
def test_failed_child_does_not_hide_other_positions(wrapper, monkeypatch, change):
    output, _, seen = wrapper
    original = delivery.execute_child

    def run(arguments, environ, directory):
        child = original(arguments, environ, directory)
        if seen[-1] == "showcase":
            if change == "nonzero_exit":
                child.returncode = 1
            elif change == "invalid_json":
                child.stdout = "child-secret diagnostic, not JSON"
            else:
                result = json.loads(child.stdout)
                if change == "wrong_scope":
                    result["real_java_requests"] = 1
                else:
                    result["cases"].pop()
                child.stdout = json.dumps(result)
        return child

    monkeypatch.setattr(delivery, "execute_child", run)
    result = delivery.check_delivery(output)
    assert not result["verified"] and result["verified_checks"] == 8
    assert len(seen) == len(result["results"]) == 9
    failed = result["results"][3]
    assert failed["status"] == "FAILED" and failed["reason"]
    assert "child-secret" not in json.dumps(result)


@pytest.mark.parametrize("error,reason", [
    (subprocess.TimeoutExpired("fixed-command", 600, output=b"partial output"), "CHILD_TIMEOUT"),
    (OSError("private process detail"), "CHILD_START_FAILED"),
])
def test_child_infrastructure_failure_retains_evidence(wrapper, monkeypatch, error, reason):
    output, _, _ = wrapper
    monkeypatch.setattr(delivery, "execute_child", lambda *a, **k: (_ for _ in ()).throw(error))
    result = delivery.check_delivery(output)
    assert not result["verified"] and result["verified_checks"] == 0
    assert len(result["results"]) == 9
    assert all(row["reason"] == reason for row in result["results"])
    assert "private process detail" not in json.dumps(result)
    if reason == "CHILD_TIMEOUT":
        assert (output / "logs/project-courses/stdout.txt").read_text() == "partial output"


def test_source_drift_stops_future_children_without_dropping_positions(wrapper, monkeypatch):
    output, _, seen = wrapper
    identities = iter([{"commit": "fixed"}, {"commit": "fixed"}, {"commit": "changed"}])
    monkeypatch.setattr(delivery, "checkout_identity", lambda root: next(identities))
    result = delivery.check_delivery(output)
    assert not result["verified"] and not result["source_unchanged"]
    assert seen == ["project-courses"] and result["verified_checks"] == 1
    assert len(result["results"]) == 9
    assert all(row["status"] == "NOT_ATTEMPTED" for row in result["results"][1:])


def test_boolean_or_incomplete_summary_cannot_be_accepted():
    check = delivery.delivery_checks(delivery.PROJECT_ROOT / "runs/example")[0]
    assert not delivery.accepted(check, {**check.expected, "configuration_ready": 1})
    assert not delivery.accepted(check, {"configuration_ready": True})
    assert not delivery.accepted(check, [])


def test_output_outside_private_runs_is_rejected_before_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(delivery, "PROJECT_ROOT", tmp_path / "repository")
    output = tmp_path / "outside"
    with pytest.raises(ValueError, match="repository runs"):
        delivery.check_delivery(output)
    assert not output.exists()


@pytest.mark.parametrize("cleanup_fault", ["tree", "kill", "wait"])
@pytest.mark.parametrize("unreadable_capture", [False, True])
def test_unconfirmed_cleanup_stops_following_checks_and_retains_partial_output(
    wrapper, monkeypatch, cleanup_fault, unreadable_capture,
):
    output, checks, seen = wrapper
    fake = delivery.execute_child
    started = []

    class Child:
        pid, returncode = 123, None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout):
            if timeout == 600 or cleanup_fault == "wait":
                raise subprocess.TimeoutExpired("fixed", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            if cleanup_fault == "kill":
                raise PermissionError()
            self.returncode = -9

    def launch(*args, **kwargs):
        kwargs["stdout"].write(b"partial evidence")
        return Child()

    def run(arguments, environ, directory):
        started.append(arguments)
        return (execute_child(arguments, environ, directory) if arguments == checks[0].arguments
                else fake(arguments, environ, directory))

    monkeypatch.setattr(delivery, "execute_child", run)
    monkeypatch.setattr(delivery.subprocess, "Popen", launch)
    monkeypatch.setattr(delivery.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=int(cleanup_fault == "tree")))

    def signal_tree(*args):
        if cleanup_fault == "tree":
            raise PermissionError()

    open_file = type(output).open
    rejected = []

    def capture(path, *args, **kwargs):
        if (unreadable_capture and not rejected and path.name == "process-stdout.txt"
                and args == ("rb",)):
            rejected.append(path)
            raise PermissionError()
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(delivery.os, "killpg", signal_tree, raising=False)
    monkeypatch.setattr(type(output), "open", capture)
    result = delivery.check_delivery(output)
    assert started == [checks[0].arguments] and not seen
    assert not result["verified"] and len(result["results"]) == 9
    assert result["results"][0]["reason"] == "CHILD_CLEANUP_UNCONFIRMED"
    assert result["cleanup_unconfirmed"] is True
    assert all(row["status"] == "NOT_ATTEMPTED" for row in result["results"][1:])
    assert (output / "logs/project-courses/stdout.txt").read_text() == (
        "" if unreadable_capture else "partial evidence")
    assert (output / "logs/project-courses/process-stdout.txt").read_bytes() == b"partial evidence"


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_timeout_stops_only_own_process_tree_and_preserves_output(monkeypatch, tmp_path, platform):
    events = []

    class Child:
        pid, returncode, attempts = 123, None, 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, timeout):
            self.attempts += 1
            if self.attempts == 1:
                assert timeout == 600
                raise subprocess.TimeoutExpired("fixed", timeout, output=b"partial")
            assert timeout == 15
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            events.append("kill-root")
            self.returncode = -9

    def launch(command, **kwargs):
        assert command == [delivery.sys.executable, "fixed-script.py"]
        assert kwargs["start_new_session"] == (platform != "nt")
        kwargs["stdout"].write(b"retained stdout")
        kwargs["stderr"].write(b"retained stderr")
        return Child()

    def stop_tree(command, **kwargs):
        events.append(command)
        return SimpleNamespace(returncode=0)

    with monkeypatch.context() as patch:
        patch.setattr(delivery.os, "name", platform)
        patch.setattr(delivery.subprocess, "Popen", launch)
        patch.setattr(delivery.subprocess, "run", stop_tree)
        patch.setattr(delivery.os, "killpg", lambda pid, sig: events.append((pid, sig)),
                      raising=False)
        patch.setattr(delivery.signal, "SIGKILL", 9, raising=False)
        error_type = (subprocess.TimeoutExpired if platform == "nt"
                      else delivery.ChildCleanupUnconfirmed)
        with pytest.raises(error_type) as caught:
            delivery.execute_child(("fixed-script.py",), {}, tmp_path)
    assert caught.value.stdout == "retained stdout" and caught.value.stderr == "retained stderr"
    assert events == ([["taskkill", "/PID", "123", "/T", "/F"], "kill-root"]
                      if platform == "nt" else [(123, 9), "kill-root"])
