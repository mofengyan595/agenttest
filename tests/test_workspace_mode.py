"""The normal product starts a real adapter; offline simulation is explicit."""

import runpy
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_product_default_uses_real_adapter_and_has_no_builtin_project_requirement(tmp_path):
    from agenttest.contracts import load_openapi
    from agenttest.generic_intake import GenericSetup, build_generic_project
    from agenttest.llm_planner import OpenAICompatiblePlanner

    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                                / "scripts/serve_web_workspace.py"))
    config = tmp_path / "model.env"
    config.write_text("OPENAI_API_KEY=unit-test-only\nOPENAI_BASE_URL=https://model.example/v1\n"
                      "AGENTTEST_MODEL=unit-model\nAGENTTEST_TARGET_BEARER=target-unit-only\n",
                      encoding="utf-8")
    svc = module["workspace"](tmp_path / "workspace", model_env=config)
    try:
        assert not svc.synthetic and svc.generic_intake and svc.projects == {}
        assert svc.model_status() == "CONFIGURED_MODEL"
        assert svc.environ["AGENTTEST_TARGET_BEARER"] == "target-unit-only"
        raw = ('{"openapi":"3.0.3","info":{"title":"Test","version":"1"},'
               '"paths":{"/new":{"get":{"operationId":"readNew",'
               '"responses":{"200":{"description":"ok"}}}}}}')
        setup = GenericSetup.model_validate(dict(base_url="http://127.0.0.1:18901",
            isolated_confirmed=True, operations=[dict(operation_id="readNew",
                alias="读取新接口", statuses=[200])]))
        project = build_generic_project("web_new", raw, load_openapi(raw), setup)
        assert isinstance(svc.planner_factory(project), OpenAICompatiblePlanner)
    finally:
        await svc.close()
    unavailable = module["workspace"](tmp_path / "unavailable", model_env=tmp_path / "missing.env")
    try:
        assert unavailable.model_status() == "UNAVAILABLE"
        assert not unavailable.synthetic
    finally:
        await unavailable.close()
    demo = module["workspace"](tmp_path / "demo", demo=True)
    try:
        assert demo.synthetic and not demo.generic_intake
        assert demo.model_status() == "SIMULATED"
    finally:
        await demo.close()


def test_target_environment_is_literal_scoped_and_process_values_win(tmp_path):
    from agenttest.generic_intake import IntakeRole
    from agenttest.goal_server import target_environment

    path = tmp_path / "target.env"
    path.write_text("AGENTTEST_TARGET_BEARER='file-only'\nOTHER_SETTING=ignored\n",
                    encoding="utf-8")
    assert target_environment(path, {"AGENTTEST_TARGET_BEARER": "process-only"}) == {
        "AGENTTEST_TARGET_BEARER": "process-only"}
    with pytest.raises(ValueError):
        IntakeRole(alias="tester", bearer_env="OPENAI_API_KEY")
    path.write_text("AGENTTEST_TARGET_BEARER=${OTHER_SECRET}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        target_environment(path, {})
