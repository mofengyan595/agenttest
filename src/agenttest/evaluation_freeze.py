"""Operator-only evaluation manifest and drift checks, with no credential serialization."""

import json
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from agenttest import llm_planner
from agenttest.evaluation import TaskSuite
from agenttest.llm_planner import ModelSettings, PlannerDraft
from agenttest.report import fingerprint


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(["git", *arguments], cwd=root, capture_output=True,
                                text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Evaluation checkout unavailable") from None
    return result.stdout.strip()


def _version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def public_model_config(settings: ModelSettings) -> dict:
    # Explicit allowlist, never model_dump() a settings object containing SecretStr.
    return {"model": settings.model, "base_url_sha256": fingerprint(settings.base_url),
            "protocol": settings.protocol, "timeout": settings.timeout,
            "max_input_tokens": settings.max_input_tokens,
            "max_output_tokens": settings.max_output_tokens,
            "max_total_tokens": settings.max_total_tokens,
            "max_response_bytes": settings.max_response_bytes}


class EvaluationFreeze:
    def __init__(self, root: Path, settings: ModelSettings, suite: TaskSuite,
                 max_calls: int, max_tokens: int):
        self.root, self.settings, self.suite = root, settings, suite
        if _git(root, "status", "--porcelain", "--untracked-files=normal"):
            raise ValueError("Evaluation requires a clean committed checkout")
        if not 1 <= max_calls <= 20 or max_tokens < 1 or not suite.reviewed:
            raise ValueError("Evaluation requires explicit reviewed budgets")
        self.manifest = {
            "version": 1, "frozen_at": datetime.now(timezone.utc).isoformat(),
            "code_commit": _git(root, "rev-parse", "HEAD"),
            "tree_sha": _git(root, "rev-parse", "HEAD^{tree}"),
            "prompt_sha256": fingerprint(llm_planner.SYSTEM_PROMPT),
            "draft_schema_sha256": fingerprint(PlannerDraft.model_json_schema()),
            "suite_sha256": fingerprint(suite.model_dump()),
            "model_config": public_model_config(settings),
            "generation": {"retry_count": 0, "serial": True,
                           "temperature": 0 if settings.protocol == "chatecnu" else None,
                           "thinking": "disabled" if settings.protocol == "chatecnu" else None,
                           "response_format": "json_schema"},
            "max_live_calls": max_calls, "max_total_tokens": max_tokens,
            "python": sys.version.split()[0],
            "dependencies": {name: _version(name) for name in
                             ("langgraph", "httpx", "pydantic")},
        }
        self._snapshot = json.dumps(self.manifest, sort_keys=True)

    def verify(self) -> None:
        frozen = json.loads(self._snapshot)
        if (_git(self.root, "rev-parse", "HEAD") != frozen["code_commit"]
                or _git(self.root, "status", "--porcelain", "--untracked-files=normal")
                or fingerprint(llm_planner.SYSTEM_PROMPT) != frozen["prompt_sha256"]
                or fingerprint(PlannerDraft.model_json_schema()) != frozen["draft_schema_sha256"]
                or fingerprint(self.suite.model_dump()) != frozen["suite_sha256"]
                or public_model_config(self.settings) != frozen["model_config"]
                or json.dumps(self.manifest, sort_keys=True) != self._snapshot):
            raise ValueError("Evaluation freeze changed; no further calls allowed")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as destination:
            destination.write(json.dumps(self.manifest, ensure_ascii=False, indent=2))
