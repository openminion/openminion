from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.llm.providers.behavior import resolve_behavior_profile
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.llm.providers.normalization import normalize_provider_response


def test_normalize_provider_response_coerces_object_shape() -> None:
    raw = SimpleNamespace(
        output_text="Tool call pending",
        model="",
        usage=SimpleNamespace(input_tokens=11, output_tokens=7, total_tokens=None),
        tool_calls=[
            SimpleNamespace(
                id="call-1",
                name="weather",
                arguments={"city": "San Francisco"},
                status="requested",
            )
        ],
        finish_reason="tool_calls",
        normalization={},
    )

    normalized = normalize_provider_response(
        raw,
        provider_name="openrouter",
        allowed_tool_names=["weather"],
    )

    assert normalized.text == "Tool call pending"
    assert normalized.model == "openrouter"
    assert normalized.usage["prompt_tokens"] == 11
    assert normalized.usage["completion_tokens"] == 7
    assert normalized.usage["total_tokens"] == 18
    assert len(normalized.tool_calls) == 1
    assert normalized.tool_calls[0].name == "weather"
    assert normalized.tool_calls[0].source == "requested"
    assert normalized.normalization.get("response_normalized") is True


def test_normalize_provider_response_does_not_recover_tool_call_from_text() -> None:
    raw = {
        "text": '{"name":"weather","arguments":{"city":"Tokyo"}}',
        "model": "gpt-test",
        "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        "tool_calls": [],
        "finish_reason": "stop",
        "normalization": {},
    }

    normalized = normalize_provider_response(
        raw,
        provider_name="openrouter",
        allowed_tool_names=["weather"],
    )

    assert normalized.tool_calls == []
    assert normalized.text == '{"name":"weather","arguments":{"city":"Tokyo"}}'
    assert normalized.finish_reason == "stop"


def test_normalize_provider_response_recovers_empty_payload() -> None:
    raw = SimpleNamespace(
        output_text="",
        model="",
        usage={},
        tool_calls=[],
        finish_reason="",
        normalization={},
    )

    normalized = normalize_provider_response(raw, provider_name="openrouter")

    assert normalized.text.startswith("I received an empty response from OpenRouter")
    assert normalized.finish_reason == "empty_payload_recovered"
    assert normalized.normalization.get("empty_payload_recovered") is True
    assert normalized.normalization.get("normalization_profile") == "openrouter-default"


def test_normalize_provider_response_applies_model_specific_finish_reason_mapping() -> (
    None
):
    raw = {
        "text": "",
        "model": "openrouter/oss20b",
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
        "tool_calls": [
            {"name": "weather", "arguments": {"city": "Seoul"}, "id": "call-2"}
        ],
        "finish_reason": "tool_use",
        "normalization": {},
    }

    normalized = normalize_provider_response(
        raw,
        provider_name="openrouter",
        model_name="openrouter/oss20b",
        allowed_tool_names=["weather"],
    )

    assert normalized.finish_reason == "tool_calls"
    assert normalized.normalization.get("normalization_profile") == "openrouter-oss"


def test_normalize_provider_response_sanitizes_rejected_channel_envelope() -> None:
    raw = {
        "text": '<|start|>assistant<|channel|>commentary to=tool.not_allowed <|message|>{"q":"x"}<|call|>',
        "model": "openrouter/test",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "tool_calls": [],
        "finish_reason": "stop",
        "normalization": {},
    }

    normalized = normalize_provider_response(
        raw,
        provider_name="openrouter",
        model_name="openrouter/test",
        allowed_tool_names=["web.search"],
    )

    assert normalized.normalization.get("envelope_sanitized") is True
    assert normalized.text.startswith("[system: UNEXECUTABLE_TOOL_ENVELOPE]")
    assert "Reason: unparseable" in normalized.text
    assert "<|start|>" not in normalized.text
    assert "<|channel|>" not in normalized.text


def test_normalize_provider_response_sanitizes_rejected_minimax_markup() -> None:
    raw = {
        "text": '<tool name="not.allowed"><parameter name="q">x</parameter></tool>',
        "model": "minimax",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "tool_calls": [],
        "finish_reason": "stop",
        "normalization": {},
    }

    normalized = normalize_provider_response(
        raw,
        provider_name="openrouter",
        model_name="minimax",
        allowed_tool_names=["web.search"],
    )

    assert normalized.normalization.get("envelope_sanitized") is True
    assert normalized.text.startswith("[system: UNEXECUTABLE_TOOL_ENVELOPE]")
    assert "Reason: unparseable" in normalized.text
    assert "<tool name=" not in normalized.text
    assert "<parameter name=" not in normalized.text


def test_normalize_provider_response_preserves_parseable_minimax_bracket_markup() -> (
    None
):
    raw = {
        "text": (
            "I'll inspect the files.\n"
            "[TOOL_CALL]\n"
            '{tool => "file.read", args => { --path "pyproject.toml" }}\n'
            "[/TOOL_CALL]"
        ),
        "model": "MiniMax-M2.7",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "tool_calls": [],
        "finish_reason": "stop",
        "normalization": {},
    }

    normalized = normalize_provider_response(
        raw,
        provider_name="openai",
        model_name="MiniMax-M2.7",
        allowed_tool_names=["file.read"],
    )

    assert normalized.normalization.get("envelope_sanitized") is not True
    assert "[TOOL_CALL]" in normalized.text


def test_normalize_provider_response_preserves_native_v2_shape_without_fallback() -> (
    None
):
    native = normalize_provider_response(
        {
            "text": "",
            "model": "openrouter/test",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "weather",
                    "arguments": {"location": "Tokyo"},
                    "source": "native",
                }
            ],
            "finish_reason": "tool_calls",
            "normalization": {},
        },
        provider_name="openrouter",
        model_name="openrouter/test",
        allowed_tool_names=["weather"],
    )
    fallback = normalize_provider_response(
        {
            "text": '{"tool_calls":[{"name":"weather","arguments":{"location":"Tokyo"}}]}',
            "model": "openrouter/test",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "tool_calls": [],
            "finish_reason": "stop",
            "normalization": {},
        },
        provider_name="openrouter",
        model_name="openrouter/test",
        allowed_tool_names=["weather"],
    )

    assert len(native.tool_calls) == 1
    assert fallback.tool_calls == []
    assert (
        fallback.text
        == '{"tool_calls":[{"name":"weather","arguments":{"location":"Tokyo"}}]}'
    )

    native_call = native.tool_calls[0]
    payload = {
        "id": native_call.id,
        "name": native_call.name,
        "arguments": native_call.arguments,
        "source": native_call.source,
        "depends_on": native_call.depends_on,
    }
    assert set(payload.keys()) == {
        "id",
        "name",
        "arguments",
        "source",
        "depends_on",
    }
    assert isinstance(payload["arguments"], dict)
    assert isinstance(payload["depends_on"], list)
    assert native_call.name == "weather"
    assert native_call.arguments == {"location": "Tokyo"}


def test_normalize_provider_response_preserves_depends_on_from_native_calls() -> None:
    normalized = normalize_provider_response(
        {
            "text": "",
            "model": "openrouter/test",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "tool_calls": [
                ProviderToolCall(
                    id="call-2",
                    name="weather",
                    arguments={"location": "Seoul"},
                    source="native",
                    depends_on=["call-1", "call-1", "call-0"],
                )
            ],
            "finish_reason": "tool_calls",
            "normalization": {},
        },
        provider_name="openrouter",
        model_name="openrouter/test",
        allowed_tool_names=["weather"],
    )

    assert len(normalized.tool_calls) == 1
    assert normalized.tool_calls[0].depends_on == ["call-1", "call-0"]


def test_normalize_provider_response_profile_argument_matches_direct_resolution() -> (
    None
):
    raw = SimpleNamespace(
        output_text="",
        model="openrouter/oss20b",
        usage={},
        tool_calls=[],
        finish_reason="",
        normalization={},
    )
    profile = resolve_behavior_profile(
        provider="openrouter",
        model="openrouter/oss20b",
        base_url="https://openrouter.ai/api/v1",
    )

    via_profile = normalize_provider_response(
        raw,
        provider_name="openrouter",
        model_name="openrouter/oss20b",
        profile=profile.normalization_profile,
    )
    direct = normalize_provider_response(
        raw,
        provider_name="openrouter",
        model_name="openrouter/oss20b",
    )

    assert via_profile.text == direct.text
    assert via_profile.finish_reason == direct.finish_reason
    assert via_profile.normalization.get("normalization_profile") == "openrouter-oss"
