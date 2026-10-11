import json
import subprocess

import pytest

from agenttest import llm_planner
from agenttest.demo import PROJECT_ROOT
from agenttest.evaluation import TaskSuite, save_evaluation
from agenttest.evaluation_freeze import EvaluationFreeze
from agenttest.llm_planner import ModelSettings
from agenttest.report import fingerprint


@pytest.fixture
def committed(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)

    git("init")
    (tmp_path / "code.py").write_text("# synthetic fixture\n", encoding="utf-8")
    git("add", "code.py")
    git("-c", "user.name=Test Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", "test fixture")
    return tmp_path


def freeze(root):
    settings = ModelSettings(model="ecnu-plus", base_url="https://model.invalid/v1",
                             api_key="synthetic-private-key", protocol="chatecnu")
    suite = TaskSuite.load(PROJECT_ROOT / "examples/nl-tasks.json")
    return EvaluationFreeze(root, settings, suite, 18, 60_000)


def test_manifest_is_secret_free_and_immutable_output(committed, tmp_path):
    frozen = freeze(committed)
    frozen.verify()
    encoded = json.dumps(frozen.manifest)
    assert "synthetic-private-key" not in encoded and "api_key" not in encoded
    assert frozen.manifest["max_live_calls"] == 18
    assert frozen.manifest["generation"] == {
        "retry_count": 0, "serial": True, "temperature": 0,
        "thinking": "disabled", "response_format": "json_schema"}
    path = tmp_path / "manifest.json"
    frozen.save(path)
    with pytest.raises(FileExistsError):
        frozen.save(path)


@pytest.mark.parametrize("drift", ["code", "prompt", "task", "model", "manifest"])
def test_drift_stops_further_calls(committed, monkeypatch, drift):
    frozen = freeze(committed)
    if drift == "code":
        (committed / "code.py").write_text("# changed\n", encoding="utf-8")
    elif drift == "prompt":
        monkeypatch.setattr(llm_planner, "SYSTEM_PROMPT", "changed prompt")
    elif drift == "task":
        frozen.suite.cases[0].user_task = "changed task"
    elif drift == "model":
        frozen.settings.model = "changed-model"
    else:
        frozen.manifest["max_live_calls"] = 20
    with pytest.raises(ValueError, match="freeze changed"):
        frozen.verify()


def test_dirty_checkout_cannot_start_real_evaluation(committed):
    (committed / "uncommitted.py").write_text("# new\n", encoding="utf-8")
    with pytest.raises(ValueError, match="clean committed"):
        freeze(committed)


def test_historical_results_and_approved_suite_are_preserved(tmp_path):
    suite = TaskSuite.load(PROJECT_ROOT / "examples/nl-tasks.json")
    assert fingerprint(suite.model_dump()) == (
        "28f54185a1a44ade1adebd15d9c63d1f7d002a6a66eb10a236e165483f4e66a0")
    first = json.loads((PROJECT_ROOT / "docs/stage2a-results.json").read_text(encoding="utf-8"))
    assert first["baselines"]["real"]["end_to_end_success_rate"] == 13 / 18
    assert len(first["real_tasks"]) == 18
    path = tmp_path / "historic.json"
    save_evaluation({"value": "first"}, path)
    with pytest.raises(FileExistsError):
        save_evaluation({"value": "replacement"}, path)
    assert json.loads(path.read_text())["value"] == "first"
