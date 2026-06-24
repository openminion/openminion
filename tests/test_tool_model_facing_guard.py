from __future__ import annotations

from openminion.modules.llm.providers.tool_calling import (
    extract_fallback_tool_calls_from_text,
)
from openminion.modules.tool import build_default_tool_registry


_LEGACY_MODEL_FACING_NAMES = {
    "list_files",
    "read_file",
    "write_file",
    "find_files",
    "run_command",
    "web_search",
    "web_fetch",
    "lookup_weather",
    "start_process",
    "stop_process",
    "process_status",
    "process_output",
}


def test_model_facing_provider_specs_are_canonical_only() -> None:
    registry = build_default_tool_registry()
    names = {spec.name for spec in registry.model_provider_specs()}
    assert not (names & _LEGACY_MODEL_FACING_NAMES)
    assert "web.search" in names
    assert "weather" in names
    assert "time" in names
    assert "location" in names
    assert "ip.public" in names
    assert "ip.local" in names
    assert "file.list_dir" in names
    assert "exec.run" in names
    assert "reactions.set" not in names
    assert "reactions.list" not in names


def test_model_runtime_dispatch_map_exposes_runtime_targets() -> None:
    registry = build_default_tool_registry()
    dispatch_map = registry.model_runtime_dispatch_map()
    assert dispatch_map["weather"]["runtime_tool_name"] == "weather"
    assert dispatch_map["weather"]["runtime_binding_id"] == "runtime.weather.current"
    assert dispatch_map["time"]["runtime_tool_name"] == "time.now"


def test_fallback_parser_keeps_legacy_aliases_out_of_model_surface() -> None:
    text = '{"tool_calls":[{"name":"run_command","arguments":{"command":"pwd"}}]}'
    calls = extract_fallback_tool_calls_from_text(
        text,
        provider_name="openrouter",
        model_name="guard-test",
        allowed_tool_names={"exec.run"},
    )
    assert [call.name for call in calls] == ["exec.run"]

    text = '{"tool_calls":[{"name":"lookup_weather","arguments":{"location":"San Francisco"}}]}'
    calls = extract_fallback_tool_calls_from_text(
        text,
        provider_name="openrouter",
        model_name="guard-test",
        allowed_tool_names={"weather"},
    )
    assert [call.name for call in calls] == ["weather"]

    text = '{"tool_calls":[{"name":"location.get","arguments":{}}]}'
    calls = extract_fallback_tool_calls_from_text(
        text,
        provider_name="openrouter",
        model_name="guard-test",
        allowed_tool_names={"location"},
    )
    assert calls == []
