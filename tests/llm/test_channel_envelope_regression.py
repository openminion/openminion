import pytest

from openminion.modules.llm.providers.tool_calling import (
    _CHANNEL_ENVELOPE_RE,
    _extract_channel_envelope_calls,
    extract_fallback_tool_calls_from_text,
)
from openminion.modules.llm.providers.tool_calling.base import ERROR_UNKNOWN_TOOL_NAME
from openminion.modules.llm.providers.tool_calling.normalizer import (
    normalize_tool_calls,
)


@pytest.mark.parametrize(
    ("text", "expected_path"),
    [
        (
            '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>',
            ".",
        ),
        (
            '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "/tmp"}<|call|>',
            "/tmp",
        ),
        (
            '<|start|>assistant<|channel|>commentary to=tool.FILE.LIST_DIR <|constrain|>json<|message|>{"path": "."}<|call|>',
            ".",
        ),
    ],
)
def test_channel_envelope_parsing_contract(text: str, expected_path: str) -> None:
    calls = _extract_channel_envelope_calls(text, allowed_tool_names=["file.list_dir"])
    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"
    assert calls[0].arguments.get("path") == expected_path


def test_fallback_parser_uses_channel_envelope() -> None:
    text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
    calls = extract_fallback_tool_calls_from_text(
        text, allowed_tool_names=["file.list_dir"]
    )
    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"


def test_tool_request_wrapper_is_rejected() -> None:
    text = (
        "<|start|>assistant<|channel|>commentary to=tool.request "
        '<|constrain|>json<|message|>{"command":"search","query":"iran war latest","top_k":5}<|call|>'
    )
    calls = extract_fallback_tool_calls_from_text(
        text, allowed_tool_names=["web.search"]
    )
    assert calls == []


def test_missing_json_args_rejected() -> None:
    text = "<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>not valid json<|call|>"
    calls = _extract_channel_envelope_calls(text, allowed_tool_names=["file.list_dir"])
    assert len(calls) == 0


def test_unallowed_tool_rejected() -> None:
    text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
    calls = _extract_channel_envelope_calls(text, allowed_tool_names=["file.read"])
    assert len(calls) == 0


def test_malformed_envelope_no_crash() -> None:
    text = "<|start|>assistant<|channel|>commentary to=tool."
    calls = _extract_channel_envelope_calls(text, allowed_tool_names=["file.list_dir"])
    assert len(calls) == 0


def test_empty_text_returns_empty() -> None:
    calls = _extract_channel_envelope_calls("", allowed_tool_names=["file.list_dir"])
    assert len(calls) == 0


def test_channel_envelope_regex_matches_expected() -> None:
    text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
    match = _CHANNEL_ENVELOPE_RE.search(text)
    assert match is not None
    assert match.group("tool_name") == "file.list_dir"
    assert match.group("json_args") == '{"path": "."}'


def test_channel_envelope_takes_precedence() -> None:
    text = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
    calls = extract_fallback_tool_calls_from_text(
        text, allowed_tool_names=["file.list_dir"]
    )
    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"


def test_json_fallback_when_no_envelope() -> None:
    text = '{"tool_calls":[{"name":"file.list_dir","arguments":{"path":"."}}]}'
    calls = extract_fallback_tool_calls_from_text(
        text, allowed_tool_names=["file.list_dir"]
    )
    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"


@pytest.mark.parametrize(
    ("text", "expected_raw_name"),
    [
        (
            (
                "I can help.\n"
                '<tool name="not.allowed">'
                '<parameter name="q">x</parameter>'
                "</tool>"
            ),
            "not.allowed",
        ),
        (
            (
                "<|start|>assistant<|channel|>commentary to=tool.not_allowed "
                '<|constrain|>json<|message|>{"q":"x"}<|call|>'
            ),
            "tool.not_allowed",
        ),
        (
            (
                '<tool name="secret.admin">'
                '<parameter name="q">x</parameter>'
                "</tool>"
            ),
            "secret.admin",
        ),
    ],
)
def test_malformed_or_hidden_envelopes_report_structural_errors(
    text: str,
    expected_raw_name: str,
) -> None:
    result = normalize_tool_calls(
        assistant_text=text,
        provider_name="openrouter",
        model_name="MiniMax-M2.7",
        allowed_tool_names=["web.search"],
    )

    assert result.calls == []
    assert [error.code for error in result.errors] == [ERROR_UNKNOWN_TOOL_NAME]
    assert result.errors[0].details["tool_name"] == expected_raw_name
    assert result.metadata["tool_parse_strategy"] == "none"
    assert "tool_parse_errors" in result.metadata


def test_multiple_envelopes_preserve_executable_structural_call() -> None:
    result = normalize_tool_calls(
        assistant_text=(
            '<tool name="web.search"><parameter name="query">x</parameter></tool>'
            ' <tool name="not.allowed"><parameter name="q">y</parameter></tool>'
        ),
        provider_name="openrouter",
        model_name="MiniMax-M2.7",
        allowed_tool_names=["web.search"],
    )

    assert len(result.calls) == 1
    assert result.calls[0].name == "web.search"
    assert result.calls[0].arguments == {"query": "x"}
    assert result.errors == []
    assert result.metadata["tool_parse_strategy"] == "fallback"
    assert result.metadata["tool_parse_format"] == "minimax_xml"


def test_provider_fallback_cli_mode_reports_parse_mode() -> None:
    result = normalize_tool_calls(
        assistant_text='tool web.search {"query":"x"}',
        provider_name="openrouter",
        model_name="MiniMax-M2.7",
        allowed_tool_names=["web.search"],
    )

    assert len(result.calls) == 1
    assert result.calls[0].name == "web.search"
    assert result.metadata["tool_parse_strategy"] == "fallback"
    assert result.metadata["tool_parse_format"] == "cli_command"
