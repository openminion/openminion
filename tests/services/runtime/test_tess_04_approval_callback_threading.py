from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.services.runtime.ingress import _run_gateway_once
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.services.agent.execution.progress import execute_allowed_tool_calls


@pytest.mark.asyncio
async def test_run_gateway_once_passes_approval_callback_to_run_once() -> None:

    sentinel: Any = object()

    captured: dict[str, Any] = {}

    async def fake_run_once(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "ok"

    gateway = MagicMock()
    gateway.run_once = fake_run_once

    await _run_gateway_once(
        gateway=gateway,
        channel="console",
        target="cli-chat",
        message="hello",
        session_id=None,
        idempotency_key=None,
        request_id=None,
        inbound_metadata=None,
        deliver=False,
        approval_callback=sentinel,
    )
    assert captured.get("approval_callback") is sentinel


@pytest.mark.asyncio
async def test_run_gateway_once_omits_approval_callback_when_none() -> None:

    captured: dict[str, Any] = {}

    async def fake_run_once(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "ok"

    gateway = MagicMock()
    gateway.run_once = fake_run_once

    await _run_gateway_once(
        gateway=gateway,
        channel="console",
        target="cli-chat",
        message="hello",
        session_id=None,
        idempotency_key=None,
        request_id=None,
        inbound_metadata=None,
        deliver=False,
    )
    assert "approval_callback" not in captured


def test_threading_signatures_accept_approval_callback() -> None:

    import inspect

    from openminion.api.runtime import APIRuntime
    from openminion.api.turns import run_turn
    from openminion.services.runtime.ingress import (
        execute_runtime_turn,
        run_turn_payload,
        _run_gateway_once,
    )

    for fn in (
        run_turn,
        APIRuntime.run_turn,
        run_turn_payload,
        execute_runtime_turn,
        _run_gateway_once,
    ):
        sig = inspect.signature(fn)
        assert "approval_callback" in sig.parameters, (
            f"{fn.__qualname__} is missing approval_callback in its signature"
        )


def test_approved_call_receives_per_call_confirmation_context() -> None:
    captured: list[ToolExecutionContext] = []

    class Tools:
        def execute_calls(self, calls, *, context):
            captured.append(context)
            return MagicMock(
                results=[
                    ToolExecutionResult(
                        tool_name=calls[0].name,
                        ok=True,
                        content="ok",
                    )
                ]
            )

    call = ProviderToolCall(
        name="ops.command.run",
        arguments={"plan_id": "plan-1", "plan_hash": "a" * 64},
        id="call-1",
        approval_id="call-1",
    )
    base_context = ToolExecutionContext(channel="console", target="cli-chat")

    results = execute_allowed_tool_calls(
        MagicMock(tools=Tools()),
        MagicMock(progress_callback=None),
        allowed_calls=[call],
        context=base_context,
    )

    assert results[0].ok is True
    assert base_context.confirm is False
    assert captured[0].confirm is True
    assert captured[0].metadata["confirmation_grant_id"] == "call-1"
