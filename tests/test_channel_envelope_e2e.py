from openminion.modules.llm.providers.tool_calling import (
    extract_fallback_tool_calls_from_text_with_metadata,
)


def test_e2e_channel_envelope_parse_and_metadata() -> None:
    envelope = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'

    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        envelope,
        allowed_tool_names=["file.list_dir", "file.read"],
    )

    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"
    assert calls[0].arguments.get("path") == "."

    assert metadata["fallback_parse_mode"] == "channel_envelope"
    assert metadata["fallback_tool_name_raw"] == "file.list_dir"


def test_e2e_json_fallback_with_metadata() -> None:
    json_text = '{"tool_calls":[{"name":"file.list_dir","arguments":{"path":"/tmp"}}]}'

    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        json_text,
        allowed_tool_names=["file.list_dir"],
    )

    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"
    assert metadata["fallback_parse_mode"] == "json_payload"
    assert metadata["fallback_tool_name_raw"] == "file.list_dir"


def test_e2e_no_tool_calls_empty_metadata() -> None:
    text = "This is just regular assistant text with no tool calls."
    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        text,
        allowed_tool_names=["file.list_dir"],
    )
    assert len(calls) == 0
    assert metadata.get("fallback_parse_mode") is None
    assert metadata.get("fallback_tool_name_raw") is None


def test_e2e_multiple_channel_envelopes_all_parsed() -> None:
    envelope = (
        '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "/a"}<|call|>\n'
        '<|start|>assistant<|channel|>commentary to=tool.file.read <|constrain|>json<|message|>{"path": "/a/file.txt"}<|call|>'
    )

    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        envelope,
        allowed_tool_names=["file.list_dir", "file.read"],
    )
    assert len(calls) == 2
    assert calls[0].name == "file.list_dir"
    assert calls[1].name == "file.read"
    assert metadata["fallback_parse_mode"] == "channel_envelope"
    assert metadata["fallback_tool_name_raw"] == "file.list_dir"


def test_e2e_legacy_alias_list_files_rejected() -> None:
    envelope = '<|start|>assistant<|channel|>commentary to=tool.list_files <|constrain|>json<|message|>{"path": "."}<|call|>'
    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        envelope,
        allowed_tool_names=["file.list_dir"],
    )
    assert len(calls) == 0
    assert metadata.get("fallback_parse_mode") == "channel_envelope"
    assert metadata.get("fallback_tool_name_raw") == "list_files"


def test_e2e_legacy_alias_read_file_rejected() -> None:
    envelope = '<|start|>assistant<|channel|>commentary to=tool.read_file <|constrain|>json<|message|>{"file_path": "/tmp/test.txt"}<|call|>'
    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        envelope,
        allowed_tool_names=["file.read"],
    )
    assert len(calls) == 0
    assert metadata.get("fallback_parse_mode") == "channel_envelope"
    assert metadata.get("fallback_tool_name_raw") == "read_file"


def test_e2e_policy_blocked_tool_rejected() -> None:
    envelope = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        envelope,
        allowed_tool_names=["file.read", "file.find"],
    )
    assert len(calls) == 0
    assert metadata["fallback_parse_mode"] == "channel_envelope"
    assert metadata["fallback_tool_name_raw"] == "file.list_dir"
    assert metadata["envelope_target_raw"] == "tool.file.list_dir"
    assert metadata["envelope_rejected_reason"] == "tool_not_allowed"


def test_envelope_parsed_not_leaked_as_text() -> None:
    envelope = '<|start|>assistant<|channel|>commentary to=tool.file.list_dir <|constrain|>json<|message|>{"path": "."}<|call|>'
    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        envelope,
        allowed_tool_names=["file.list_dir"],
    )
    assert len(calls) == 1
    assert calls[0].name == "file.list_dir"
    assert metadata["fallback_parse_mode"] == "channel_envelope"


def test_json_payload_detected_as_envelope() -> None:
    from openminion.services.agent import _looks_like_tool_call_envelope_text

    json_text = '{"tool_calls":[{"name":"file.list_dir","arguments":{"path":"."}}]}'
    assert _looks_like_tool_call_envelope_text(json_text) is True
