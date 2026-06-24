from __future__ import annotations

import asyncio
from unittest import mock

from openminion.base.types import Message
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


async def _collect_stream(stream):  # noqa: ANN001, ANN201
    events = []
    async for item in stream:
        events.append(item)
    return events
