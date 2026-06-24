import pytest

from openminion.modules.llm.providers.tool_calling import (
    _CHANNEL_ENVELOPE_RE,
    _extract_channel_envelope_calls,
    extract_fallback_tool_calls_from_text,
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
