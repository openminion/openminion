from __future__ import annotations

from openminion.modules.llm.providers.behavior import resolve_behavior_profile
from openminion.modules.llm.providers.tool_calling.registry import (
    parse_fallback_tool_calls,
    parse_structured_tool_call_envelopes,
    resolve_fallback_parser_plugins,
)


def test_resolve_fallback_parser_plugins_returns_typed_tuple():
    plugins = resolve_fallback_parser_plugins(
        provider_name="openai", model_name="gpt-4", fallback_parser_policy="full"
    )
    assert isinstance(plugins, tuple)
    assert all(isinstance(name, str) for name in plugins)


def test_profile_driven_selection_is_independent_of_provider_name_routing():
    selection = resolve_fallback_parser_plugins(
        provider_name="openai", model_name="gpt-4", fallback_parser_policy="full"
    )

    result_a = parse_fallback_tool_calls(
        "no tool call here",
        provider_name="openai",
        model_name="gpt-4",
        parser_plugin_selection=selection,
    )
    result_b = parse_fallback_tool_calls(
        "no tool call here",
        provider_name="anthropic",  # different provider — must not change result
        model_name="claude-sonnet",
        parser_plugin_selection=selection,
    )
    # Both produce no parsed calls AND identical metadata for the same selection.
    assert result_a.calls == result_b.calls
    assert (result_a.metadata or {}) == (result_b.metadata or {})


def test_profile_driven_structured_selection_independent_of_model_name_routing():
    selection = resolve_fallback_parser_plugins(
        provider_name="openai",
        model_name="gpt-4",
        fallback_parser_policy="structured",
    )

    result_a = parse_structured_tool_call_envelopes(
        "no envelope here",
        provider_name="openai",
        model_name="gpt-4",
        parser_plugin_selection=selection,
    )
    result_b = parse_structured_tool_call_envelopes(
        "no envelope here",
        provider_name="openai",
        model_name="minimax-m2",  # different model — must not change result
        parser_plugin_selection=selection,
    )
    assert result_a.calls == result_b.calls


def test_resolve_behavior_profile_supplies_parser_plugin_selection():
    profile = resolve_behavior_profile(provider="openai", model="gpt-4", base_url="")
    assert profile.parser_plugin_selection is not None
    # Even empty tuple is acceptable (typed empty selection); never None.


def test_minimax_profile_supplies_distinct_parser_plugin_selection():
    profile = resolve_behavior_profile(
        provider="openai",
        model="minimax-m2",
        base_url="https://api.minimax.io/v1",
    )
    selection = profile.parser_plugin_selection
    assert selection is not None
    assert len(selection) > 0
    # Selection MUST include at least one minimax-flavored parser by name.
    assert any("minimax" in name.lower() for name in selection)
