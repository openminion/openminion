"""A default-agent selection error is not a reason to build a different provider."""

from __future__ import annotations

import logging

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.base.config.base import UnknownProfileError
from openminion.base.config.core import AgentProfileConfig
from openminion.modules.llm.providers.factory import (
    build_provider,
    build_runtime_llm_handle,
)


def _config(
    agents: dict[str, AgentProfileConfig], default_agent: str = ""
) -> OpenMinionConfig:
    return OpenMinionConfig(agents=agents, default_agent=default_agent)


def _two_agents() -> dict[str, AgentProfileConfig]:
    return {
        "alpha": AgentProfileConfig(name="alpha", provider="ollama"),
        "beta": AgentProfileConfig(name="beta", provider="ollama"),
    }


def _logger() -> logging.Logger:
    return logging.getLogger("test.provider.factory")


@pytest.mark.parametrize("default_agent", ["", "missing-profile"])
@pytest.mark.parametrize("entry", ["build_provider", "build_runtime_llm_handle"])
def test_a_selection_error_is_not_replaced_by_echo(
    default_agent: str, entry: str
) -> None:
    """A multi-agent config with no usable default must fail, not build echo."""
    config = _config(_two_agents(), default_agent=default_agent)
    build = build_provider if entry == "build_provider" else build_runtime_llm_handle

    with pytest.raises(UnknownProfileError):
        build(config, _logger())


def test_an_explicitly_selected_multi_agent_config_still_builds() -> None:
    config = _config(_two_agents(), default_agent="beta")

    assert build_provider(config, _logger()).name == "ollama"


def test_a_single_agent_config_still_builds_without_a_default() -> None:
    config = _config({"only": AgentProfileConfig(name="only", provider="ollama")})

    assert build_provider(config, _logger()).name == "ollama"


def test_an_echo_agent_still_builds_echo() -> None:
    config = _config(
        {"quiet": AgentProfileConfig(name="quiet", provider="echo")},
        default_agent="quiet",
    )

    assert build_provider(config, _logger()).name == "echo"
