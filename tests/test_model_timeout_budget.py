"""Per-call model timeout is operator-settable, bounded, and recorded for audit."""

import pytest

from agenttest.llm_planner import ModelSettings, model_timeout

ENV = {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "https://example.test", "AGENTTEST_MODEL": "m"}


def test_default_per_call_ceiling_is_thirty_seconds():
    assert ModelSettings.from_environment(ENV).timeout == 30.0
    assert model_timeout(None) == 30.0 and model_timeout("") == 30.0


def test_operator_can_set_a_bounded_ceiling():
    assert ModelSettings.from_environment(dict(ENV, AGENTTEST_MODEL_TIMEOUT="45")).timeout == 45.0
    assert ModelSettings.from_environment(dict(ENV, AGENTTEST_MODEL_TIMEOUT="120")).timeout == 120.0


@pytest.mark.parametrize("raw", ["0", "-1", "121", "1e9", "abc", " 45 s"])
def test_out_of_range_or_unparseable_ceiling_is_refused(raw):
    with pytest.raises(ValueError):
        ModelSettings.from_environment(dict(ENV, AGENTTEST_MODEL_TIMEOUT=raw))


def test_env_file_accepts_the_same_literal_setting(tmp_path):
    path = tmp_path / "model.env"
    path.write_text("\n".join(f"{key}={value}" for key, value in dict(
        ENV, AGENTTEST_MODEL_TIMEOUT="45").items()), encoding="utf-8")
    assert ModelSettings.from_env_file(path, environ={}).timeout == 45.0
    path.write_text(path.read_text(encoding="utf-8").replace("=45", "=999"), encoding="utf-8")
    with pytest.raises(ValueError):
        ModelSettings.from_env_file(path, environ={})
