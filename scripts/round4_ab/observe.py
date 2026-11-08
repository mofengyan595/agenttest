"""Identical external recorder; return values, exceptions and executor inputs stay unchanged."""

import hashlib
import json
import os
import runpy
import sys
from pathlib import Path
from uuid import uuid4

from budget import install


def scrub(value, secrets):
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(s in key.lower() for s in ("password", "authorization", "cookie", "api_key"))
                else scrub(item, secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED_SECRET]")
    return value


def install_observers(folder, secrets):
    from agenttest.executor import Executor
    from agenttest.product_design import TestDesignModel
    from agenttest.product_focus import TestFocusModel
    from agenttest.product_probe import ProductProbeModel

    def save(row):
        folder.mkdir(exist_ok=True)
        with (folder / (uuid4().hex + ".json")).open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(scrub(row, secrets), stream, ensure_ascii=False)

    def wrap_prepare(original):
        def prepared(self, context):
            result = original(self, context)
            raw = result.encode() if isinstance(result, str) else result
            save(
                dict(
                    kind="model_request",
                    model_class=type(self).__name__,
                    request_sha256=self.stats.request_sha256,
                    request_body_sha256=hashlib.sha256(raw).hexdigest(),
                    request=json.loads(raw),
                )
            )
            return result

        return prepared

    def wrap_decode(original):
        def decoded(self, content, context):
            row = dict(
                kind="model_response",
                model_class=type(self).__name__,
                request_sha256=self.stats.request_sha256,
                raw_text=content,
            )
            try:
                row["wire"] = json.loads(content)
            except (ValueError, TypeError):
                row["wire"] = None
            try:
                result = original(self, content, context)
                captured = json.loads(result) if isinstance(result, (str, bytes)) else result
                row.update(decode="ACCEPTED", decoded=captured)
                return result
            except Exception as error:
                diagnostic = getattr(error, "diagnostic", None)
                row.update(
                    decode="REJECTED",
                    diagnostic=diagnostic.model_dump(mode="json") if diagnostic else None,
                    exception_type=type(error).__name__,
                )
                raise
            finally:
                save(row)

        return decoded

    for model in (TestFocusModel, TestDesignModel, ProductProbeModel):
        model.prepare_request = wrap_prepare(model.prepare_request)
        model._decode_output = wrap_decode(model._decode_output)
    original_execute = Executor.run

    async def execute(self, scenario, *, report=None):
        # Record the actual typed input at the trusted executor boundary, after coalescing.
        if report is not None:
            save(
                dict(
                    kind="execution_plan",
                    run_id=report.run_id,
                    plan=scenario.model_dump(mode="json"),
                )
            )
        result = await original_execute(self, scenario, report=report)
        if report is None:
            save(
                dict(
                    kind="execution_plan",
                    run_id=result.run_id,
                    plan=scenario.model_dump(mode="json"),
                )
            )
        return result

    Executor.run = execute


if __name__ == "__main__":
    source = Path(sys.argv.pop(1)).resolve()
    import agenttest
    from agenttest.goal_server import target_environment
    from agenttest.llm_planner import ModelSettings

    assert Path(agenttest.__file__).resolve().is_relative_to(source / "src")
    env_file = Path(sys.argv[sys.argv.index("--model-env") + 1])
    settings = ModelSettings.from_env_file(env_file)
    secrets = [
        settings.api_key.get_secret_value(),
        *[
            v
            for k, v in target_environment(env_file, {}).items()
            if k.startswith("AGENTTEST_TARGET_")
        ],
    ]
    install()
    install_observers(Path(os.environ["AGENTTEST_EVAL_OBSERVATIONS"]), secrets)
    sys.argv[0] = str(source / "scripts/serve_web_workspace.py")
    runpy.run_path(sys.argv[0], run_name="__main__")
