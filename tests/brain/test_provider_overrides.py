from __future__ import annotations

from openminion.modules.brain.adapters.llm.overrides import (
    provider_retry_override_table,
    resolve_provider_retry_override,
)


def test_provider_retry_override_matches_openai_structured_thinking_lane() -> None:
    result = resolve_provider_retry_override(
        provider_name="openai",
        model_name="MiniMax-M2.5",
        purpose="decide",
        thinking="minimal",
        tool_choice={"type": "function", "function": {"name": "submit_output"}},
        tool_names=["submit_output"],
        metadata={"purpose": "decide"},
    )

    assert result.matched is True
    assert result.override_id == "openai_structured_thinking_tool_choice_retry"
    assert result.retry_tool_choice == "auto"


def test_provider_retry_override_matches_qwen_structured_thinking_lane() -> None:
    result = resolve_provider_retry_override(
        provider_name="openai",
        model_name="qwen3.5-plus",
        purpose="decide",
        thinking="minimal",
        tool_choice={"type": "function", "function": {"name": "submit_output"}},
        tool_names=["submit_output"],
        metadata={"purpose": "decide"},
    )

    assert result.matched is True
    assert result.override_id == "openai_structured_thinking_tool_choice_retry"
    assert result.retry_tool_choice == "auto"


def test_provider_retry_override_respects_disable_switches() -> None:
    disabled_by_metadata = resolve_provider_retry_override(
        provider_name="openai",
        model_name="MiniMax-M2.5",
        purpose="decide",
        thinking="minimal",
        tool_choice={"type": "function", "function": {"name": "submit_output"}},
        tool_names=["submit_output"],
        metadata={"provider_override_mode": "disabled"},
    )
    disabled_by_env = resolve_provider_retry_override(
        provider_name="openai",
        model_name="MiniMax-M2.5",
        purpose="decide",
        thinking="minimal",
        tool_choice={"type": "function", "function": {"name": "submit_output"}},
        tool_names=["submit_output"],
        metadata={"purpose": "decide"},
        env={"OPENMINION_DISABLE_PROVIDER_OVERRIDES": "1"},
    )

    assert disabled_by_metadata.disabled is True
    assert disabled_by_metadata.matched is False
    assert disabled_by_env.disabled is True
    assert disabled_by_env.matched is False


def test_provider_retry_override_table_is_explicit_and_serializable() -> None:
    table = provider_retry_override_table()

    assert table
    assert table[0]["override_id"]
    assert "rollback_hint" in table[0]
    assert table[0]["provider_names"] == ["openai"]
