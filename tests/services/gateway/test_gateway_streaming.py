from __future__ import annotations

import asyncio
import time
from unittest import mock

from openminion.base.types import Message
from openminion.modules.brain.streaming import turn_progress_from_llm_stream_event
from openminion.modules.llm.schemas import LLMStreamEvent
from openminion.services.gateway.streaming import (
    gateway_stream_event_from_turn_chunk,
)
from tests.services.gateway._gateway_service_support import GatewayServiceTestCase


class GatewayServiceStreamingTests(GatewayServiceTestCase):
    def test_handle_message_streaming_yields_structural_events_and_final_message(
        self,
    ) -> None:
        async def _fake_handle_message(**kwargs):  # type: ignore[no-untyped-def]
            progress_callback = kwargs.get("progress_callback")
            assert callable(progress_callback)
            progress_callback(
                {
                    "trace_id": "trace-1",
                    "status_key": "working",
                    "label": "Working...",
                }
            )
            progress_callback(
                {
                    "kind": "tool_started",
                    "trace_id": "trace-1",
                    "tool_name": "web.search",
                    "args": {"q": "latest"},
                }
            )
            progress_callback(
                {
                    "kind": "tool_completed",
                    "trace_id": "trace-1",
                    "tool_name": "web.search",
                    "args": {"q": "latest"},
                    "ok": True,
                    "duration_ms": 42,
                    "content": "done",
                }
            )
            return Message(
                channel="console",
                target="cli-chat",
                body="final answer",
                metadata={"run_id": "trace-1"},
            )

        with mock.patch.object(
            self.gateway, "handle_message", side_effect=_fake_handle_message
        ):
            events = asyncio.run(
                _collect_stream(
                    self.gateway.handle_message_streaming(
                        channel="console",
                        target="cli-chat",
                        body="hello",
                    )
                )
            )

        assert [event.kind for event in events] == [
            "status",
            "tool_call_started",
            "tool_call_completed",
            "final_message",
        ]
        assert events[-1].final_message is not None
        assert events[-1].final_message["body"] == "final answer"

    def test_handle_message_streaming_maps_turn_token_chunks_before_final(self) -> None:
        async def _fake_handle_message(**kwargs):  # type: ignore[no-untyped-def]
            progress_callback = kwargs.get("progress_callback")
            assert callable(progress_callback)
            provider_events = (
                LLMStreamEvent(type="delta", delta_text="Hello"),
                LLMStreamEvent(type="delta", delta_text=" world"),
                LLMStreamEvent(type="done"),
            )
            for provider_event in provider_events:
                chunk = turn_progress_from_llm_stream_event(
                    provider_event,
                    trace_id="trace-token",
                )
                if chunk is not None:
                    progress_callback(chunk)
            return Message(
                channel="console",
                target="cli-chat",
                body="Hello world",
                metadata={"run_id": "trace-token"},
            )

        with mock.patch.object(
            self.gateway, "handle_message", side_effect=_fake_handle_message
        ):
            events = asyncio.run(
                _collect_stream(
                    self.gateway.handle_message_streaming(
                        channel="console",
                        target="cli-chat",
                        body="hello",
                    )
                )
            )

        assert [event.kind for event in events] == [
            "assistant_token",
            "assistant_token",
            "final_message",
        ]
        assert [event.text for event in events[:2]] == ["Hello", " world"]

    def test_brain_stream_mapping_ignores_non_delta_and_empty_events(self) -> None:
        assert (
            turn_progress_from_llm_stream_event(
                LLMStreamEvent(type="done"), trace_id="trace-token"
            )
            is None
        )
        assert (
            turn_progress_from_llm_stream_event(
                LLMStreamEvent(type="delta", delta_text=""),
                trace_id="trace-token",
            )
            is None
        )

    def test_gateway_stream_event_from_turn_chunk_maps_chunk_kinds(self) -> None:
        tool_started = gateway_stream_event_from_turn_chunk(
            {
                "trace_id": "trace-2",
                "kind": "tool_started",
                "data": {"tool_name": "time.now", "args": {}},
                "ts": "2026-05-09T00:00:00Z",
            }
        )
        assert tool_started is not None
        assert tool_started.kind == "tool_call_started"
        assert tool_started.tool_name == "time.now"

        budget = gateway_stream_event_from_turn_chunk(
            {
                "trace_id": "trace-2",
                "kind": "budget_event",
                "data": {"event_type": "budget.extended", "cap": 8},
                "ts": "2026-05-09T00:00:01Z",
            }
        )
        assert budget is not None
        assert budget.kind == "budget_event"
        assert budget.budget_event_type == "budget.extended"

        flat_delta = gateway_stream_event_from_turn_chunk(
            {
                "trace_id": "trace-2",
                "kind": "delta",
                "delta_text": "streamed",
            }
        )
        assert flat_delta is not None
        assert flat_delta.kind == "assistant_token"
        assert flat_delta.text == "streamed"

        malformed_delta = gateway_stream_event_from_turn_chunk(
            {"trace_id": "trace-2", "kind": "delta", "data": {}}
        )
        assert malformed_delta is None

    def test_handle_message_streaming_flushes_progress_during_blocking_turn(
        self,
    ) -> None:
        async def _fake_handle_message(**kwargs):  # type: ignore[no-untyped-def]
            progress_callback = kwargs.get("progress_callback")
            assert callable(progress_callback)
            progress_callback(
                {
                    "kind": "tool_started",
                    "trace_id": "trace-blocking",
                    "tool_name": "weather",
                    "args": {"location": "Tokyo"},
                }
            )
            time.sleep(0.25)
            progress_callback(
                {
                    "kind": "tool_completed",
                    "trace_id": "trace-blocking",
                    "tool_name": "weather",
                    "args": {"location": "Tokyo"},
                    "ok": True,
                    "duration_ms": 250,
                    "content": "Tokyo weather now.",
                }
            )
            return Message(
                channel="console",
                target="cli-chat",
                body="final answer",
                metadata={"run_id": "trace-blocking"},
            )

        with mock.patch.object(
            self.gateway, "handle_message", side_effect=_fake_handle_message
        ):
            first_elapsed, events = asyncio.run(
                _collect_stream_with_first_elapsed(
                    self.gateway.handle_message_streaming(
                        channel="console",
                        target="cli-chat",
                        body="weather please",
                    )
                )
            )

        assert first_elapsed < 0.15
        assert [event.kind for event in events] == [
            "tool_call_started",
            "tool_call_completed",
            "final_message",
        ]

    def test_handle_message_streaming_runs_approval_callback_on_stream_loop(
        self,
    ) -> None:
        approval_calls: list[str] = []

        async def _approval_callback(tool_name, arguments, context):  # type: ignore[no-untyped-def]
            del arguments, context
            approval_calls.append(str(tool_name))
            return True

        async def _fake_handle_message(**kwargs):  # type: ignore[no-untyped-def]
            approval_callback = kwargs.get("approval_callback")
            assert callable(approval_callback)
            approved = await approval_callback("file.write", {"path": "x"}, None)
            return Message(
                channel="console",
                target="cli-chat",
                body=f"approved={approved}",
                metadata={"run_id": "trace-approval"},
            )

        with mock.patch.object(
            self.gateway, "handle_message", side_effect=_fake_handle_message
        ):
            events = asyncio.run(
                _collect_stream(
                    self.gateway.handle_message_streaming(
                        channel="console",
                        target="cli-chat",
                        body="write file",
                        approval_callback=_approval_callback,
                    )
                )
            )

        assert approval_calls == ["file.write"]
        assert events[-1].final_message is not None
        assert events[-1].final_message["body"] == "approved=True"


async def _collect_stream(stream):  # noqa: ANN001, ANN201
    events = []
    async for item in stream:
        events.append(item)
    return events


async def _collect_stream_with_first_elapsed(stream):  # noqa: ANN001, ANN201
    events = []
    first_elapsed = None
    started_at = time.perf_counter()
    async for item in stream:
        if first_elapsed is None:
            first_elapsed = time.perf_counter() - started_at
        events.append(item)
    assert first_elapsed is not None
    return first_elapsed, events
