from __future__ import annotations

import json

from openminion.modules.telemetry.export.otel import _attributes_for_event
from openminion.modules.telemetry.schemas import TelemetryEvent


def _make_llm_event(
    event_type: str = "llm.call.completed",
    payload: dict | None = None,
) -> TelemetryEvent:
    return TelemetryEvent(
        session_id="sess-1",
        turn_id="turn-1",
        event_type=event_type,
        data=payload or {},
    )


def test_llm_call_completed_emits_gen_ai_attributes() -> None:
    event = _make_llm_event(
        payload={
            "model": "claude-opus-4-7",
            "provider": "anthropic",
            "llm_call_id": "call-abc",
            "usage": {"input_tokens": 1234, "output_tokens": 567},
            "finish_reason": "end_turn",
        },
    )
    attrs = _attributes_for_event(event, include_assistant_body=False)
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.system"] == "anthropic"
    assert attrs["gen_ai.request.model"] == "claude-opus-4-7"
    assert attrs["gen_ai.usage.input_tokens"] == 1234
    assert attrs["gen_ai.usage.output_tokens"] == 567
    assert attrs["gen_ai.response.id"] == "call-abc"
    assert json.loads(attrs["gen_ai.response.finish_reasons"]) == ["end_turn"]


def test_gen_ai_system_inferred_from_model_when_provider_missing() -> None:
    event = _make_llm_event(payload={"model": "gpt-4o"})
    attrs = _attributes_for_event(event, include_assistant_body=False)
    assert attrs["gen_ai.system"] == "openai"


def test_gen_ai_prompt_completion_token_keys_supported() -> None:
    event = _make_llm_event(
        payload={
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        }
    )
    attrs = _attributes_for_event(event, include_assistant_body=False)
    assert attrs["gen_ai.usage.input_tokens"] == 100
    assert attrs["gen_ai.usage.output_tokens"] == 50


def test_non_llm_events_omit_gen_ai_attributes() -> None:
    event = _make_llm_event(
        event_type="tool.executed",
        payload={"model": "claude-opus-4-7", "usage": {"input_tokens": 10}},
    )
    attrs = _attributes_for_event(event, include_assistant_body=False)
    assert not any(key.startswith("gen_ai.") for key in attrs)


def test_missing_usage_keys_omit_token_attributes_not_zero_fill() -> None:
    event = _make_llm_event(payload={"model": "claude-opus-4-7"})
    attrs = _attributes_for_event(event, include_assistant_body=False)
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.output_tokens" not in attrs
    assert attrs["gen_ai.request.model"] == "claude-opus-4-7"
    assert attrs["gen_ai.system"] == "anthropic"


def test_negative_path_failed_llm_call_finish_reason_error() -> None:
    event = _make_llm_event(
        payload={
            "model": "claude-opus-4-7",
            "stop_reason": "error",
        }
    )
    attrs = _attributes_for_event(event, include_assistant_body=False)
    assert json.loads(attrs["gen_ai.response.finish_reasons"]) == ["error"]
