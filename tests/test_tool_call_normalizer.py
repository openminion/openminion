from __future__ import annotations

from openminion.modules.llm.providers.tool_calling import (
    ERROR_INVALID_TOOL_ARGUMENTS,
    ERROR_UNKNOWN_TOOL_NAME,
    ERROR_UNPARSEABLE_TOOL_ENVELOPE,
    PARSE_ERRORS_KEY,
    PARSE_FORMAT_KEY,
    PARSE_STRATEGY_KEY,
    NormalizedToolCallResult,
    ToolCallNormalizer,
    normalize_tool_calls,
)


def _error_codes(result: NormalizedToolCallResult) -> list[str]:
    return [error.code for error in result.errors]


def test_normalize_openai_native_emits_native_strategy_and_format() -> None:
    result = normalize_tool_calls(
        message_payload={
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "web.search",
                        "arguments": '{"query": "iran war sentiment"}',
                    },
                }
            ]
        },
        allowed_tool_names=["web.search"],
    )
    assert len(result.calls) == 1
    assert result.calls[0].name == "web.search"
    assert result.calls[0].arguments == {"query": "iran war sentiment"}
    assert result.metadata[PARSE_STRATEGY_KEY] == "native"
    assert result.metadata[PARSE_FORMAT_KEY] == "openai_native"
    assert PARSE_ERRORS_KEY not in result.metadata


def test_normalize_openrouter_channel_envelope_emits_fallback_strategy() -> None:
    text = (
        "some preamble "
        "<|start|>asst<|channel|>final to=tool.web.search "
        '<|constrain|>json <|message|>{"query": "x"} <|call|>'
    )
    result = normalize_tool_calls(
        assistant_text=text,
        provider_name="openrouter",
        allowed_tool_names=["web.search"],
    )
    assert len(result.calls) == 1
    assert result.calls[0].name == "web.search"
    assert result.calls[0].arguments == {"query": "x"}
    assert result.metadata[PARSE_STRATEGY_KEY] == "fallback"
    assert result.metadata[PARSE_FORMAT_KEY] == "channel_envelope"


def test_normalize_minimax_xml_emits_fallback_strategy_and_format() -> None:
    text = (
        "<minimax:tool_call>"
        '<invoke name="file.read">'
        '<parameter name="path">README.md</parameter>'
        "</invoke>"
        "</minimax:tool_call>"
    )
    result = normalize_tool_calls(
        assistant_text=text,
        model_name="minimax",
        allowed_tool_names=["file.read"],
    )
    assert len(result.calls) == 1
    assert result.calls[0].name == "file.read"
    assert result.calls[0].arguments == {"path": "README.md"}
    assert result.metadata[PARSE_STRATEGY_KEY] == "fallback"
    assert result.metadata[PARSE_FORMAT_KEY] == "minimax_xml"


def test_normalize_minimax_bracket_emits_fallback_strategy_and_format() -> None:
    text = (
        '[TOOL_CALL]{tool => "web.search", args => { --query "iran war" }}[/TOOL_CALL]'
    )
    result = normalize_tool_calls(
        assistant_text=text,
        model_name="minimax",
        allowed_tool_names=["web.search"],
    )
    assert len(result.calls) == 1
    assert result.calls[0].name == "web.search"
    assert result.metadata[PARSE_STRATEGY_KEY] == "fallback"
    assert result.metadata[PARSE_FORMAT_KEY] == "minimax_bracket"


def test_normalize_tool_call_json_wrapper_emits_fallback_strategy_and_format() -> None:
    text = '<tool_call>{"name":"exec","parameters":{"command":"pwd"}}</tool_call>'
    result = normalize_tool_calls(
        assistant_text=text,
        model_name="minimax",
        allowed_tool_names=["exec.run"],
    )
    assert len(result.calls) == 1
    assert result.calls[0].name == "exec.run"
    assert result.calls[0].arguments == {"command": "pwd"}
    assert result.metadata[PARSE_STRATEGY_KEY] == "fallback"
    assert result.metadata[PARSE_FORMAT_KEY] == "json_payload"


def test_normalize_resolves_legacy_search_alias_to_web_search() -> None:
    text = '[TOOL_CALL]{tool => "search", args => { --query "iran" }}[/TOOL_CALL]'
    result = normalize_tool_calls(
        assistant_text=text,
        model_name="minimax",
        allowed_tool_names=["web.search"],
    )
    assert len(result.calls) == 1
    assert result.calls[0].name == "web.search"


def test_normalize_rejects_unknown_native_tool_name_with_structured_error() -> None:
    result = normalize_tool_calls(
        message_payload={
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "not_a_real_tool", "arguments": "{}"},
                }
            ]
        },
        allowed_tool_names=["web.search"],
    )
    assert result.calls == []
    assert ERROR_UNKNOWN_TOOL_NAME in _error_codes(result)
    error = next(e for e in result.errors if e.code == ERROR_UNKNOWN_TOOL_NAME)
    assert error.details["tool_name"] == "not_a_real_tool"
    assert error.details["channel"] == "native"
    serialized = result.metadata[PARSE_ERRORS_KEY]
    assert any(item["code"] == ERROR_UNKNOWN_TOOL_NAME for item in serialized)


def test_normalize_rejects_unknown_fallback_tool_name_with_structured_error() -> None:
    text = (
        "some chat "
        "<|start|>asst<|channel|>final to=tool.not_a_real_tool "
        '<|constrain|>json <|message|>{"x": 1} <|call|>'
    )
    result = normalize_tool_calls(
        assistant_text=text,
        provider_name="openrouter",
        allowed_tool_names=["web.search"],
    )
    assert result.calls == []
    assert ERROR_UNKNOWN_TOOL_NAME in _error_codes(result)


def test_normalize_rejects_malformed_envelope_args_with_invalid_tool_arguments() -> (
    None
):
    text = (
        "<|start|>asst<|channel|>final to=tool.web.search "
        "<|constrain|>json <|message|>{not valid json} <|call|>"
    )
    result = normalize_tool_calls(
        assistant_text=text,
        provider_name="openrouter",
        allowed_tool_names=["web.search"],
    )
    assert result.calls == []
    assert ERROR_INVALID_TOOL_ARGUMENTS in _error_codes(result)
    error = next(e for e in result.errors if e.code == ERROR_INVALID_TOOL_ARGUMENTS)
    assert "arguments" in error.details["invalid"]
    assert error.details["hint"]


def test_normalize_blocks_unparseable_raw_envelope_with_structured_error() -> None:
    text = (
        "Here's the answer: <minimax:tool_call>"
        '<invoke name="totally.unknown">'
        "<parameter></parameter>"
        "</invoke>"
        "</minimax:tool_call>"
    )
    result = normalize_tool_calls(
        assistant_text=text,
        model_name="minimax",
        allowed_tool_names=["only.this"],
    )
    assert result.calls == []
    codes = _error_codes(result)
    assert any(
        code in {ERROR_UNKNOWN_TOOL_NAME, ERROR_UNPARSEABLE_TOOL_ENVELOPE}
        for code in codes
    )


def test_normalize_emits_unparseable_when_markup_present_with_no_tool_metadata() -> (
    None
):
    text = "Random preface <tool_code> garbage payload </tool_code> trailing"
    result = normalize_tool_calls(
        assistant_text=text,
        model_name="minimax",
        allowed_tool_names=["web.search"],
    )
    assert result.calls == []
    assert ERROR_UNPARSEABLE_TOOL_ENVELOPE in _error_codes(result)


def test_normalize_empty_inputs_returns_no_calls_and_no_errors() -> None:
    result = normalize_tool_calls(
        assistant_text="",
        message_payload=None,
        allowed_tool_names=["web.search"],
    )
    assert result.calls == []
    assert result.errors == []
    assert result.metadata[PARSE_STRATEGY_KEY] == "none"
    assert PARSE_ERRORS_KEY not in result.metadata


def test_normalize_plain_text_with_no_tool_markup_returns_no_calls() -> None:
    result = normalize_tool_calls(
        assistant_text="Hello, how can I help today?",
        allowed_tool_names=["web.search"],
    )
    assert result.calls == []
    assert result.errors == []
    assert result.metadata[PARSE_STRATEGY_KEY] == "none"


def test_module_level_normalize_matches_instance_method() -> None:
    payload = {
        "tool_calls": [
            {
                "id": "call_1",
                "function": {"name": "web.search", "arguments": '{"query": "x"}'},
            }
        ]
    }
    instance = ToolCallNormalizer().normalize(
        message_payload=payload, allowed_tool_names=["web.search"]
    )
    module = normalize_tool_calls(
        message_payload=payload, allowed_tool_names=["web.search"]
    )
    assert [call.name for call in instance.calls] == [
        call.name for call in module.calls
    ]
    assert instance.metadata[PARSE_STRATEGY_KEY] == module.metadata[PARSE_STRATEGY_KEY]
    assert instance.metadata[PARSE_FORMAT_KEY] == module.metadata[PARSE_FORMAT_KEY]
