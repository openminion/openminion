import pytest
from openminion.base.config import OpenMinionConfig
from openminion.modules.llm.providers.base import LLMProvider
from openminion.services.agent import _loop_tool_feedback, _provider_tool_call_strategy


def test_loop_tool_feedback_accepts_max_chars() -> None:
    text = _loop_tool_feedback(["alpha", "beta"], max_chars=9)
    assert text == "alpha..."


def test_loop_tool_feedback_without_limit() -> None:
    text = _loop_tool_feedback(["alpha", "beta"])
    assert text == "alpha\n\nbeta"


@pytest.fixture
def config() -> OpenMinionConfig:
    return OpenMinionConfig.from_dict(
        {
            "agents": {"openminion": {"name": "openminion", "provider": "openrouter"}},
            "default_agent": "openminion",
            "providers": {"openrouter": {"tool_call_strategy": "hybrid"}},
        }
    )


def test_provider_tool_call_strategy_supports_config_only_call(
    config: OpenMinionConfig,
) -> None:
    assert _provider_tool_call_strategy(config) == "hybrid"


def test_provider_tool_call_strategy_prefers_provider_override(
    config: OpenMinionConfig,
) -> None:
    class _Provider(LLMProvider):
        name = "fake"
        tool_call_strategy = "fallback"

        async def generate(self, request):  # pragma: no cover - protocol stub
            raise NotImplementedError

    assert _provider_tool_call_strategy(_Provider(), config) == "fallback"
