"""Awaiting an async ``@tool`` handler at the ordinary brain caller (#612)."""

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.adapters.tool import results as results_module
from openminion.modules.brain.adapters.tool.results import run_tool_spec
from openminion.modules.runtime import sync as sync_module
from openminion.modules.tool.errors import ToolRuntimeError


def _events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        results_module,
        "emit_tool_execution_event",
        lambda **event: events.append(event),
    )
    return events


def _run(handler: object, *, name: str = "async_probe") -> dict[str, Any]:
    spec = SimpleNamespace(name=name, handler=handler)
    return run_tool_spec(
        spec=spec,
        validated_args={},
        context=SimpleNamespace(tool_call_id="call-1", artifacts=[]),
        start_time=time.monotonic(),
        background_write_authorized=False,
        tool_name=name,
    )


def test_async_handler_runs_once_without_a_running_loop(monkeypatch) -> None:
    events = _events(monkeypatch)
    calls: list[int] = []

    async def handler(args, ctx):
        calls.append(1)
        return {"ok": True, "value": "done"}

    result = _run(handler)

    assert calls == [1]
    assert result["status"] == "success"
    assert result["outputs"] == {"ok": True, "value": "done"}
    assert [event["event_type"] for event in events] == [
        "tool.execution.started",
        "tool.execution.completed",
    ]


def test_async_handler_runs_once_inside_a_running_loop(monkeypatch) -> None:
    _events(monkeypatch)
    calls: list[int] = []

    async def handler(args, ctx):
        calls.append(1)
        await asyncio.sleep(0)
        return {"ok": True}

    async def caller():
        return _run(handler)

    result = asyncio.run(caller())

    assert calls == [1]
    assert result["status"] == "success"


def test_turn_cancellation_interrupts_the_await_and_runs_cleanup(monkeypatch) -> None:
    events = _events(monkeypatch)
    cleanup: list[str] = []

    async def handler(args, ctx):
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.append("ran")
        return {"ok": True}

    cancel = threading.Event()
    token = sync_module._turn_cancellations.set((cancel,))
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(asyncio.CancelledError):
            _run(handler)
    finally:
        timer.cancel()
        sync_module._turn_cancellations.reset(token)

    assert cleanup == ["ran"]
    assert [event["event_type"] for event in events] == ["tool.execution.started"]


def test_an_elapsed_turn_deadline_interrupts_the_await(monkeypatch) -> None:
    _events(monkeypatch)
    cleanup: list[str] = []

    async def handler(args, ctx):
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.append("ran")
        return {"ok": True}

    events_token = sync_module._turn_cancellations.set((threading.Event(),))
    deadline_token = sync_module._turn_deadline.set(time.monotonic() + 0.3)
    try:
        with pytest.raises(asyncio.CancelledError):
            _run(handler)
    finally:
        sync_module._turn_deadline.reset(deadline_token)
        sync_module._turn_cancellations.reset(events_token)

    assert cleanup == ["ran"]


def test_coroutine_failure_still_maps_to_exec_error(monkeypatch) -> None:
    events = _events(monkeypatch)

    async def handler(args, ctx):
        raise ValueError("boom")

    result = _run(handler)

    assert result["status"] == "error"
    assert result["error"]["code"] == "EXEC_ERROR"
    assert [event["event_type"] for event in events] == [
        "tool.execution.started",
        "tool.execution.failed",
    ]


def test_tool_runtime_error_from_a_coroutine_is_not_wrapped(monkeypatch) -> None:
    _events(monkeypatch)

    async def handler(args, ctx):
        raise ToolRuntimeError("PRECONDITION_FAILED", "no")

    with pytest.raises(ToolRuntimeError):
        _run(handler)


def test_sync_handler_neighbour_is_unchanged(monkeypatch) -> None:
    events = _events(monkeypatch)

    result = _run(lambda args, ctx: {"ok": True, "value": "sync"})

    assert result["status"] == "success"
    assert result["outputs"] == {"ok": True, "value": "sync"}
    assert [event["event_type"] for event in events] == [
        "tool.execution.started",
        "tool.execution.completed",
    ]


def test_a_decorated_tool_runs_once_with_its_validated_arguments(monkeypatch) -> None:
    from openminion.modules.tool.framework import derive_tool_specs
    from openminion.tools.decorator import tool

    _events(monkeypatch)
    received: list[tuple[str, int]] = []

    @tool(name="probe.note_writer", min_scope="READ_ONLY", idempotent=True)
    async def note_writer(note: str, repeat: int = 1) -> dict:
        received.append((note, repeat))
        return {"ok": True, "note": note}

    spec = derive_tool_specs(note_writer.tool_family_spec())[0]
    result = run_tool_spec(
        spec=spec,
        validated_args={"note": "handoff", "repeat": 3},
        context=SimpleNamespace(tool_call_id="call-decorated", artifacts=[]),
        start_time=time.monotonic(),
        background_write_authorized=False,
        tool_name=spec.name,
    )

    assert received == [("handoff", 3)]
    assert result["status"] == "success"
    assert result["outputs"]["note"] == "handoff"
