from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from openminion.base.types import Message
from openminion.services.gateway.context import (
    _maybe_apply_contextctl_call_site,
)
from openminion.services.gateway.types import TurnContext


def _make_turn_context(history: list[Message] | None = None) -> TurnContext:
    if history is None:
        history = [
            Message(
                channel="console", target="t", body="prior", metadata={"role": "user"}
            )
        ]
    return TurnContext(history=history, prior_transcript_available=True)


def _logger():
    return logging.getLogger("openminion.tests.cgwe07-guard")


@pytest.fixture
def mock_env(monkeypatch):
    env = MagicMock()
    env_state = {"CONTEXTCTL_GATEWAY_ENABLED": ""}

    def _get_bool(name: str, default: bool) -> bool:
        raw = env_state.get(name, "").strip().lower()
        if not raw:
            return default
        return raw in ("1", "true", "yes", "on")

    env.get_bool = _get_bool
    monkeypatch.setattr("openminion.services.config.resolve_services_env", lambda: env)
    return env_state


# Guard-off branch: deterministic fallback


def test_guard_off_keeps_existing_history_unchanged(mock_env):
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    with patch(
        "openminion.services.context.adapter.ContextCtlGatewayAdapter.from_env"
    ) as adapter_from_env:
        _maybe_apply_contextctl_call_site(
            turn_context=turn_ctx,
            agent_id="a1",
            agent_memory=None,
            logger=_logger(),
            session_id="s1",
            user_message="hello",
        )

    assert turn_ctx.history == original_history
    adapter_from_env.assert_not_called()


def test_guard_explicitly_false_keeps_history_unchanged(mock_env):
    mock_env["CONTEXTCTL_GATEWAY_ENABLED"] = "false"
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    with patch(
        "openminion.services.context.adapter.ContextCtlGatewayAdapter.from_env"
    ) as adapter_from_env:
        _maybe_apply_contextctl_call_site(
            turn_context=turn_ctx,
            agent_id="a1",
            agent_memory=None,
            logger=_logger(),
            session_id="s1",
            user_message="hello",
        )

    assert turn_ctx.history == original_history
    adapter_from_env.assert_not_called()


# Guard-on branch: adapter wired


def test_guard_on_constructs_adapter_and_delegates_select_history(mock_env):
    mock_env["CONTEXTCTL_GATEWAY_ENABLED"] = "true"
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    fake_adapter = MagicMock()
    fake_adapter.is_enabled = True
    fake_messages = ["fake-ctxctl-message"]
    fake_adapter.build_ctxctl_messages.return_value = fake_messages
    fake_adapter.select_history.return_value = ["delegated-history"]

    with patch(
        "openminion.services.context.adapter.ContextCtlGatewayAdapter.from_env",
        return_value=fake_adapter,
    ) as adapter_from_env:
        _maybe_apply_contextctl_call_site(
            turn_context=turn_ctx,
            agent_id="a1",
            agent_memory="mem-bridge",
            logger=_logger(),
            session_id="s1",
            user_message="hello",
        )

    adapter_from_env.assert_called_once_with(
        agent_id="a1",
        memory_client="mem-bridge",
        logger=_logger(),
    )
    fake_adapter.build_ctxctl_messages.assert_called_once_with(
        session_id="s1", agent_id="a1", query="hello"
    )
    fake_adapter.select_history.assert_called_once_with(
        history=original_history,
        session_id="s1",
        agent_id="a1",
        query="hello",
        contextctl_messages=fake_messages,
    )
    assert turn_ctx.history == ["delegated-history"]


def test_guard_on_with_none_messages_falls_back_to_existing_history(mock_env):
    mock_env["CONTEXTCTL_GATEWAY_ENABLED"] = "true"
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    fake_adapter = MagicMock()
    fake_adapter.is_enabled = True
    fake_adapter.build_ctxctl_messages.return_value = None

    with patch(
        "openminion.services.context.adapter.ContextCtlGatewayAdapter.from_env",
        return_value=fake_adapter,
    ):
        _maybe_apply_contextctl_call_site(
            turn_context=turn_ctx,
            agent_id="a1",
            agent_memory=None,
            logger=_logger(),
            session_id="s1",
            user_message="hello",
        )

    assert turn_ctx.history == original_history
    fake_adapter.select_history.assert_not_called()


def test_guard_on_with_disabled_adapter_falls_back(mock_env):
    mock_env["CONTEXTCTL_GATEWAY_ENABLED"] = "true"
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    fake_adapter = MagicMock()
    fake_adapter.is_enabled = False

    with patch(
        "openminion.services.context.adapter.ContextCtlGatewayAdapter.from_env",
        return_value=fake_adapter,
    ):
        _maybe_apply_contextctl_call_site(
            turn_context=turn_ctx,
            agent_id="a1",
            agent_memory=None,
            logger=_logger(),
            session_id="s1",
            user_message="hello",
        )

    assert turn_ctx.history == original_history
    fake_adapter.build_ctxctl_messages.assert_not_called()


def test_guard_on_with_adapter_exception_falls_back_deterministically(mock_env):
    mock_env["CONTEXTCTL_GATEWAY_ENABLED"] = "true"
    turn_ctx = _make_turn_context()
    original_history = list(turn_ctx.history)

    with patch(
        "openminion.services.context.adapter.ContextCtlGatewayAdapter.from_env",
        side_effect=RuntimeError("synthetic adapter construction failure"),
    ):
        # Must not raise.
        _maybe_apply_contextctl_call_site(
            turn_context=turn_ctx,
            agent_id="a1",
            agent_memory=None,
            logger=_logger(),
            session_id="s1",
            user_message="hello",
        )

    assert turn_ctx.history == original_history
