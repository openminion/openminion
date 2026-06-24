from __future__ import annotations

from openminion.modules.llm.providers.tool_calling import (
    extract_fallback_tool_calls_from_text,
)


def test_run_command_payload_recovers_as_exec_run() -> None:
    payload = '{"tool_calls":[{"name":"run_command","arguments":{"command":"pwd"}}]}'

    calls = extract_fallback_tool_calls_from_text(
        payload,
        provider_name="openrouter",
        model_name="canonical-surface-guard",
        allowed_tool_names={"exec.run"},
    )

    assert len(calls) == 1
    assert calls[0].name == "exec.run"
    assert calls[0].arguments.get("command") == "pwd"


def test_lookup_weather_payload_recovers_as_weather() -> None:
    payload = '{"tool_calls":[{"name":"lookup_weather","arguments":{"location":"San Francisco"}}]}'

    calls = extract_fallback_tool_calls_from_text(
        payload,
        provider_name="openrouter",
        model_name="canonical-surface-guard",
        allowed_tool_names={"weather"},
    )

    assert len(calls) == 1
    assert calls[0].name == "weather"
    assert calls[0].arguments.get("location") == "San Francisco"


def test_legacy_name_rejected_when_canonical_not_in_allowed_set() -> None:
    payload = '{"tool_calls":[{"name":"run_command","arguments":{"command":"pwd"}}]}'

    calls = extract_fallback_tool_calls_from_text(
        payload,
        provider_name="openrouter",
        model_name="canonical-surface-guard",
        allowed_tool_names={"file.read"},
    )

    assert calls == []
