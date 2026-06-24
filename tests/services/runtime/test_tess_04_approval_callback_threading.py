from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.services.runtime.ingress import _run_gateway_once


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
        _execute_gateway_turn,
        _run_gateway_once,
    )

    for fn in (
        run_turn,
        APIRuntime.run_turn,
        run_turn_payload,
        execute_runtime_turn,
        _execute_gateway_turn,
        _run_gateway_once,
    ):
        sig = inspect.signature(fn)
        assert "approval_callback" in sig.parameters, (
            f"{fn.__qualname__} is missing approval_callback in its signature"
        )


def test_chat_runtime_request_inproc_turn_threads_approval_callback() -> None:

    import openminion.cli.chat.runtime as chat_runtime_module

    captured: dict[str, Any] = {}

    def fake_run_turn(**kwargs: Any) -> dict:
        captured.update(kwargs)
        return {"ok": True, "metadata": {}, "body": ""}

    sentinel: Any = object()

    original = chat_runtime_module.run_turn
    chat_runtime_module.run_turn = fake_run_turn
    try:
        chat_runtime_module.request_inproc_turn(
            runtime=MagicMock(),
            config_path=None,
            payload={"message": "hi"},
            show_progress=False,
            approval_callback=sentinel,
        )
    finally:
        chat_runtime_module.run_turn = original
    assert captured.get("approval_callback") is sentinel
