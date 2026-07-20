from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openminion.cli.interactive.runtime import OpenMinionRuntime
from openminion.modules.llm.schemas import LLMResponse, Message, UsageInfo
from openminion.modules.telemetry.events.catalog import CHAT_PHASE_TIMING
from openminion.modules.telemetry.trace.phase_timing import (
    mark_active_chat_provider_token,
    record_active_chat_provider_call,
)


def test_interactive_runtime_records_complete_turn_timing(monkeypatch) -> None:
    events = []
    runtime = object.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(
        config=SimpleNamespace(runtime=SimpleNamespace(process_mode="single-process")),
        telemetry_service=SimpleNamespace(record_event_sync=events.append),
        logger=SimpleNamespace(warning=lambda *_args: None),
    )
    runtime._agent_id = "agent-1"
    runtime._gateway = object()
    runtime._session_id = "session-1"

    async def _send_message_impl(self, text, **_kwargs):  # noqa: ANN001
        record_active_chat_provider_call(
            purpose="entry",
            messages=[Message(role="user", content=text)],
            tools=[],
            response=LLMResponse(
                ok=True,
                provider="fixture",
                model="fixture-model",
                output_text="hello",
                usage=UsageInfo(input_tokens=7, output_tokens=1),
            ),
        )
        yield "hello"

    monkeypatch.setattr(OpenMinionRuntime, "_send_message_impl", _send_message_impl)

    async def _collect() -> list[str]:
        return [chunk async for chunk in runtime.send_message("hi")]

    assert asyncio.run(_collect()) == ["hello"]
    assert len(events) == 1
    event = events[0]
    assert event.event_type == CHAT_PHASE_TIMING
    assert event.session_id == "session-1"
    assert event.data["provider_calls_total"] == 1
    assert event.data["provider_call_purposes"] == ["entry"]
    assert event.data["provider_input_tokens"] == 7
    assert event.data["time_to_first_text_ms"] is not None
    assert event.data["transport"] == "gateway"


def test_interactive_runtime_records_provider_token_before_visible_text(
    monkeypatch,
) -> None:
    events = []
    runtime = object.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(
        config=SimpleNamespace(runtime=SimpleNamespace(process_mode="single-process")),
        telemetry_service=SimpleNamespace(record_event_sync=events.append),
        logger=SimpleNamespace(warning=lambda *_args: None),
    )
    runtime._agent_id = "agent-1"
    runtime._gateway = object()
    runtime._session_id = "session-stream"

    async def _send_message_impl(self, text, **_kwargs):  # noqa: ANN001
        del self, text
        mark_active_chat_provider_token()
        yield "hello"

    monkeypatch.setattr(OpenMinionRuntime, "_send_message_impl", _send_message_impl)

    async def _collect() -> list[str]:
        return [chunk async for chunk in runtime.send_message("hi")]

    assert asyncio.run(_collect()) == ["hello"]
    assert events[0].data["provider_token_ttft_ms"] is not None
    assert events[0].data["time_to_first_text_ms"] is not None
    assert (
        events[0].data["provider_token_ttft_ms"]
        <= events[0].data["time_to_first_text_ms"]
    )
