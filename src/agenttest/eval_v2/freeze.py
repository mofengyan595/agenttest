"""Append-only evaluation provenance; drift stops calls, never repairs inputs."""

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from agenttest import llm_planner
from agenttest.eval_v2 import EVALUATOR_VERSION, SCHEMA_VERSION
from agenttest.evaluation import save_evaluation
from agenttest.evaluation_freeze import _git, _version, public_model_config
from agenttest.llm_planner import PlannerDraft
from agenttest.report import fingerprint


def checkout_identity(root: Path) -> dict:
    names = _git(root, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    # Content hashes include untracked implementation files, unlike git diff alone.
    files = {
        name: fingerprint((root / name).read_bytes().hex())
        for name in names
        if (root / name).is_file()
    }
    return {
        "commit": _git(root, "rev-parse", "HEAD"),
        "worktree_status": _git(root, "status", "--porcelain", "--untracked-files=normal"),
        "checkout_sha256": fingerprint(files),
    }


class Freeze:
    """Uses the v1 freeze's Git/config helpers; v1 manifests and semantics stay untouched."""

    def __init__(
        self,
        root,
        suite,
        inputs,
        config,
        output,
        *,
        settings=None,
        suite_path=None,
        response_path=None,
        real=False,
    ):
        self.root, self.suite, self.inputs = root, suite, inputs
        self.config, self.settings = config, settings
        self.suite_path, self.response_path = suite_path, response_path
        self.output, self.real = output, real
        payload = self._payload()
        if real:
            suite.require_formal()
            if payload["git"]["worktree_status"]:
                raise ValueError("Real evaluation requires a clean committed checkout")
        self.manifest = {"frozen_at": datetime.now(timezone.utc).isoformat(), **payload}
        self._snapshot = json.dumps(self.manifest, sort_keys=True)

    def _payload(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "git": checkout_identity(self.root),
            "dataset_version": self.suite.dataset_version,
            "suite_sha256": fingerprint(self.suite.model_dump()),
            "tasks_sha256": fingerprint([c.task.model_dump() for c in self.suite.cases]),
            "labels_sha256": fingerprint([c.labels.model_dump() for c in self.suite.cases]),
            "suite_file_sha256": fingerprint(self.suite_path.read_bytes().hex())
            if self.suite_path
            else None,
            "responses_sha256": fingerprint(self.response_path.read_bytes().hex())
            if self.response_path
            else None,
            "inputs": self.inputs,
            "prompt_sha256": fingerprint(llm_planner.SYSTEM_PROMPT),
            "planner_schema_sha256": fingerprint(PlannerDraft.model_json_schema()),
            "evaluation_config": self.config.model_dump(mode="json"),
            "model_config": public_model_config(self.settings) if self.settings else None,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "dependencies": {
                    name: _version(name) for name in ("langgraph", "httpx", "pydantic")
                },
            },
            "output_directory": str(self.output.absolute()),
            "retries": 0,
        }

    def verify(self):
        frozen = json.loads(self._snapshot)
        current = {"frozen_at": frozen["frozen_at"], **self._payload()}
        if (
            json.dumps(current, sort_keys=True) != self._snapshot
            or json.dumps(self.manifest, sort_keys=True) != self._snapshot
        ):
            raise ValueError("EVALUATION_FREEZE_CHANGED")

    def save(self):
        save_evaluation(self.manifest, self.output / "freeze.json")
