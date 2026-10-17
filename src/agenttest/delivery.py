"""Run existing offline acceptance commands; never aggregate them as model scores."""

import json
import os
import secrets
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from agenttest.demo import PROJECT_ROOT
from agenttest.development_ab import private_output
from agenttest.eval_v2.freeze import checkout_identity
from agenttest.evaluation import save_evaluation
from agenttest.json_values import json_equal


@dataclass(frozen=True)
class DeliveryCheck:
    name: str
    arguments: tuple[str, ...]
    expected: dict


class ChildCleanupUnconfirmed(Exception):
    """A timed-out child tree could not be confirmed stopped; no more checks may start."""

    def __init__(self, stdout: str, stderr: str):
        super().__init__("CHILD_CLEANUP_UNCONFIRMED")
        self.stdout, self.stderr = stdout, stderr


def delivery_checks(output: Path) -> tuple[DeliveryCheck, ...]:
    """Commands and denominators are fixed here, not supplied by model or operator text."""
    rows = [DeliveryCheck("project-" + project, (
        "-m", "agenttest", "check-project", "--project",
        f"examples/portability/{project}/{task}.project.json"), {
            "configuration_ready": True, "issues": [], "http_requests": 0, "model_calls": 0})
        for project, task in (("courses", "read"), ("tickets", "read"), ("petclinic", "present"))]
    rows.extend((
        DeliveryCheck("showcase", ("-m", "agenttest", "showcase", "--case", "all",
            "--runs-dir", str(output / "showcase")), {
                "verified": True, "school_api_calls": 0, "real_java_requests": 0}),
        DeliveryCheck("portability", ("scripts/check_portability.py", "--source", "both",
            "--output", str(output / "portability")), {
                "verified": True, "cases": 30, "normal_cases": 12, "fault_cases": 10,
                "preflight_rejections": 8, "real_model_calls": 0, "real_java_requests": 0,
                "real_database_writes": 0, "human_reviewed": 0, "formal_eval_v2_score": None}),
        DeliveryCheck("json-literals", ("scripts/check_json_literals.py", "--repair",
            "--output", str(output / "json-literals")), {
                "source": "mock_transport", "scheduled_tasks": 2, "generation_successes": 2,
                "controls_passed": 2, "paired_detections": 2, "false_positives": 0,
                "false_negatives": 0, "inconclusive_controls": 0, "inconclusive_faults": 0,
                "not_executed": 0, "real_model_calls": 0, "usage_status": "SIMULATED",
                "human_reviewed": 0, "formal_eval_v2_score": None}),
        DeliveryCheck("fixed-regressions", ("scripts/check_regression_exports.py", "--output",
            str(output / "fixed-regressions")), {
                "verified": True, "normal_pytest_passed": 3, "fault_pytest_failed": 3,
                "real_model_calls": 0, "real_java_requests": 0}),
        DeliveryCheck("portability-archive", ("scripts/check_portability.py", "--verify",
            str(output / "portability")), {
                "archive_verified": True, "rescored_cases": 30, "real_model_calls": 0}),
        DeliveryCheck("json-literals-archive", ("scripts/check_json_literals.py", "--verify",
            str(output / "json-literals")), {
                "archive_verified": True, "rescored_runs": 6, "new_model_calls": 0}),
    ))
    return tuple(rows)


def accepted(check: DeliveryCheck, result: dict) -> bool:
    if not isinstance(result, dict) or not all(
        key in result and json_equal(result[key], value) for key, value in check.expected.items()
    ):
        return False
    if check.name == "showcase":
        cases = result.get("cases")
        return (isinstance(cases, list) and len(cases) == 5
                and [row.get("case") for row in cases if isinstance(row, dict)]
                == ["normal", "fault", "investigation", "java-mock", "mcp"]
                and all(row.get("verified") is True for row in cases))
    return True


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def execute_child(arguments: tuple[str, ...], environ: dict[str, str],
                  directory: Path) -> subprocess.CompletedProcess[str]:
    """Capture to private files so an unknown descendant cannot hold a reader pipe open."""
    command = [sys.executable, *arguments]
    stdout_path, stderr_path = directory / "process-stdout.txt", directory / "process-stderr.txt"
    timed_out, cleanup_confirmed = False, True
    with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
        child = subprocess.Popen(command, cwd=PROJECT_ROOT, env=environ,
            stdout=stdout_file, stderr=stderr_file, start_new_session=os.name != "nt")
        try:
            child.wait(timeout=600)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "nt":
                    cleanup_confirmed = subprocess.run(
                        ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=15).returncode == 0
                else:
                    # SDK children may create another session; killing this group is not proof
                    # that every descendant exited. Stop the batch conservatively on timeout.
                    cleanup_confirmed = False
                    os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                cleanup_confirmed = False
            except (OSError, subprocess.TimeoutExpired):
                cleanup_confirmed = False
            try:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                cleanup_confirmed = False
    if not cleanup_confirmed:
        captures = []
        for path in (stdout_path, stderr_path):
            try:
                with path.open("rb") as source:
                    captures.append(source.read(1_000_000).decode("utf-8", errors="replace"))
            except OSError:
                captures.append("")
        raise ChildCleanupUnconfirmed(*captures)
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    if timed_out:
        raise subprocess.TimeoutExpired(command, 600, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)


def run_check(check: DeliveryCheck, output: Path, environ: dict[str, str]) -> dict:
    """A child failure remains failed; independent checks still receive their own position."""
    directory = output / "logs" / check.name
    directory.mkdir(parents=True)
    row = {"check": check.name, "status": "FAILED", "exit_code": None,
           "evidence_directory": "logs/" + check.name, "reason": None}
    try:
        child = execute_child(check.arguments, environ, directory)
        stdout, stderr = child.stdout, child.stderr
        row["exit_code"] = child.returncode
        try:
            result = json.loads(stdout)
        except (ValueError, TypeError):
            row["reason"] = "INVALID_CHILD_SUMMARY"
        else:
            save_evaluation({"summary": result}, directory / "summary.json")
            row["reason"] = ("CHILD_EXIT_FAILED" if child.returncode != 0 else
                             None if accepted(check, result) else "CHILD_ACCEPTANCE_FAILED")
            if row["reason"] is None:
                row["status"] = "VERIFIED"
    except ChildCleanupUnconfirmed as error:
        stdout, stderr = error.stdout, error.stderr
        row["reason"] = "CHILD_CLEANUP_UNCONFIRMED"
    except subprocess.TimeoutExpired as error:
        stdout, stderr = _text(error.stdout), _text(error.stderr)
        row["reason"] = "CHILD_TIMEOUT"
    except OSError:
        stdout = stderr = ""
        row["reason"] = "CHILD_START_FAILED"
    (directory / "stdout.txt").write_text(stdout, encoding="utf-8")
    (directory / "stderr.txt").write_text(stderr, encoding="utf-8")
    return row


def check_delivery(output: Path) -> dict:
    private_output(PROJECT_ROOT, output)
    output.mkdir(parents=True, exist_ok=False)
    output = output.absolute()
    checks = delivery_checks(output)
    identity = checkout_identity(PROJECT_ROOT)
    save_evaluation({"git": identity, "checks": [
        {"name": c.name, "arguments": list(c.arguments), "expected": c.expected} for c in checks
    ]}, output / "freeze.json")
    environ = {**os.environ, "PYTEST_ADDOPTS": "", "LANGSMITH_TRACING": "false",
               "LANGCHAIN_TRACING_V2": "false", "PYTHONUTF8": "1"}
    # Only preflight reads these values; execution factories create fresh synthetic credentials.
    environ.update({key: secrets.token_urlsafe(24) for key in (
        "DEMO_ALICE_PASSWORD", "DEMO_BOB_PASSWORD",
        "TICKET_OWNER_BEARER", "TICKET_VISITOR_BEARER")})
    environ.update(DEMO_ALICE_USERNAME="alice", DEMO_BOB_USERNAME="bob")
    rows, drifted, cleanup_unconfirmed = [], False, False
    for check in checks:
        drifted = drifted or checkout_identity(PROJECT_ROOT) != identity
        blocked = ("SOURCE_CHANGED" if drifted else
                   "CHILD_CLEANUP_UNCONFIRMED" if cleanup_unconfirmed else None)
        if blocked:
            rows.append({"check": check.name, "status": "NOT_ATTEMPTED",
                         "reason": blocked, "exit_code": None,
                         "evidence_directory": None})
        else:
            rows.append(run_check(check, output, environ))
            cleanup_unconfirmed |= rows[-1]["reason"] == "CHILD_CLEANUP_UNCONFIRMED"
        save_evaluation(rows[-1], output / (check.name + ".json"))
    unchanged = not drifted and checkout_identity(PROJECT_ROOT) == identity
    result = {"classification": "OFFLINE_DELIVERY_ACCEPTANCE", "verified": unchanged and all(
        row["status"] == "VERIFIED" for row in rows), "source_unchanged": unchanged,
        "cleanup_unconfirmed": cleanup_unconfirmed,
        "scheduled_checks": len(checks), "verified_checks": sum(
            row["status"] == "VERIFIED" for row in rows),
        "real_model_calls": 0, "real_java_requests": 0, "real_database_writes": 0,
        "model_usage": "FAKE_OR_SIMULATED", "human_reviewed": 0, "formal_eval_v2_score": None,
        "results": rows}
    save_evaluation(result, output / "results.json")
    (output / "summary.md").write_text(
        "# 统一离线验收\n\n只验证固定回复与执行机制，不是模型成功率或正式独立评测。\n\n"
        + "| 检查 | 验收状态 |\n| --- | --- |\n"
        + "".join(f"| {row['check']} | {row['status']} |\n" for row in rows), encoding="utf-8")
    return result
