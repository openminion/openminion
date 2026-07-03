from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openminion.modules.controlplane.channels.telegram.polling import (
    TelegramPollingRunner,
)
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.services.security.policy import SecurityPolicyEngine

from tests.controlplane.telegram.integration.transports import (
    DeterministicTelegramTransport,
)
from tests.integration.test_unified_config_bootstrap import (
    _close_runtime,
    _make_config,
)


def _build_runtime_with_rate_limit(
    tmp_path: Path,
    *,
    chat_limit: int | None = None,
    user_limit: int | None = None,
    session_limit: int | None = None,
):
    config = _make_config(tmp_path, mode="polling")
    telegram = config.channels["telegram"]
    telegram["access"] = {
        "dmPolicy": "allowlist",
        "allowFromUserIds": [456],
        "groupPolicy": "deny",
    }
    telegram["pairing"] = {"enabled": False, "mode": "off"}
    cp = config.channels["controlplane"]
    if chat_limit is not None:
        cp["rate_limit_chat_limit"] = chat_limit
    if user_limit is not None:
        cp["rate_limit_user_limit"] = user_limit
    if session_limit is not None:
        cp["rate_limit_session_limit"] = session_limit

    lifecycle = LifecycleService.from_config(
        config,
        config_path=str(tmp_path / "config.json"),
        home_root=tmp_path,
        data_root=tmp_path / ".openminion",
    )
    runtime = lifecycle.build(
        security_policy=SecurityPolicyEngine(),
        load_tool_plugins=False,
    )
    return runtime


def _patch_transport(runner: Any, transport: DeterministicTelegramTransport) -> None:
    runner._api = transport.api
    runner._delivery._api = transport.api


def _audit_events(runner: Any) -> list[dict[str, Any]]:
    audit_logger = getattr(runner, "_audit_logger", None)
    if audit_logger is None:
        return []
    events = getattr(audit_logger, "events", None)
    if events is None:
        return []
    out: list[dict[str, Any]] = []
    for ev in events:
        out.append(
            {
                "event_type": getattr(ev, "event_type", None),
                "outcome": getattr(ev, "outcome", None),
                "details": dict(getattr(ev, "details", {}) or {}),
            }
        )
    return out


def _outbox_rows(store: Any) -> list[dict[str, Any]]:
    return store._inbox_outbox._rs.query_dicts(  # type: ignore[attr-defined]
        "SELECT outbox_id, status FROM cp_outbox",
    )


def test_rate_limiter_wired_chat_limit_blocks_excess(tmp_path: Path) -> None:
    runtime = _build_runtime_with_rate_limit(tmp_path, chat_limit=2)
    try:
        runner = runtime.channels.get("telegram")
        assert isinstance(runner, TelegramPollingRunner)
        assert runner._rate_limiter is not None, (
            "lifecycle did not wire rate limiter into telegram runner"
        )

        transport = DeterministicTelegramTransport(bot_token="token")
        _patch_transport(runner, transport)

        for i in range(3):
            transport.inject_message(
                chat_id=123,
                user_id=456,
                text=f"msg-{i}",
                message_id=100 + i,
            )

        processed = runner.run_once()
        assert processed == 3

        store = runner._store
        rows = _outbox_rows(store)
        assert len(rows) == 2, rows

        worker = runner._outbox_worker
        for _ in range(4):
            result = worker.run_once()
            if result is None:
                break

        outbound = transport.get_outbound_texts()
        assert len(outbound) == 3, outbound
        bodies = sorted(outbound)
        assert any("Rate limit exceeded" in s for s in bodies), bodies

        events = _audit_events(runner)
        rl_events = [
            ev for ev in events if ev["event_type"] == "cp.rate_limit.exceeded"
        ]
        assert len(rl_events) == 1, events
        assert (
            rl_events[0]["details"].get("reason") == "rate limit exceeded for chat_id"
        )
        assert rl_events[0]["outcome"] == "denied"
        assert rl_events[0]["details"].get("session_id"), rl_events[0]
        assert str(rl_events[0]["details"].get("chat_id")) == "123"

        enqueue_events = [
            ev for ev in events if ev["event_type"] == "cp.outbox.enqueued"
        ]
        assert len(enqueue_events) == 2, events
    finally:
        _close_runtime(runtime)


def test_rate_limiter_wired_user_limit_blocks(tmp_path: Path) -> None:
    runtime = _build_runtime_with_rate_limit(
        tmp_path,
        chat_limit=10,
        user_limit=2,
        session_limit=10,
    )
    try:
        runner = runtime.channels.get("telegram")
        transport = DeterministicTelegramTransport(bot_token="token")
        _patch_transport(runner, transport)

        for i in range(3):
            transport.inject_message(
                chat_id=123,
                user_id=456,
                text=f"u-msg-{i}",
                message_id=200 + i,
            )

        runner.run_once()

        events = _audit_events(runner)
        rl_events = [
            ev for ev in events if ev["event_type"] == "cp.rate_limit.exceeded"
        ]
        assert len(rl_events) == 1, events
        assert (
            rl_events[0]["details"].get("reason") == "rate limit exceeded for user_id"
        ), rl_events
    finally:
        _close_runtime(runtime)


def test_rate_limiter_wired_session_limit_blocks(tmp_path: Path) -> None:
    runtime = _build_runtime_with_rate_limit(
        tmp_path,
        chat_limit=10,
        user_limit=10,
        session_limit=2,
    )
    try:
        runner = runtime.channels.get("telegram")
        transport = DeterministicTelegramTransport(bot_token="token")
        _patch_transport(runner, transport)

        for i in range(3):
            transport.inject_message(
                chat_id=123,
                user_id=456,
                text=f"s-msg-{i}",
                message_id=300 + i,
            )

        runner.run_once()

        events = _audit_events(runner)
        rl_events = [
            ev for ev in events if ev["event_type"] == "cp.rate_limit.exceeded"
        ]
        assert len(rl_events) == 1, events
        assert (
            rl_events[0]["details"].get("reason")
            == "rate limit exceeded for session_id"
        ), rl_events
    finally:
        _close_runtime(runtime)


def test_rate_limiter_disabled_when_store_lacks_increment(tmp_path: Path) -> None:
    runtime = _build_runtime_with_rate_limit(tmp_path, chat_limit=1)
    try:
        runner = runtime.channels.get("telegram")

        class _NoLimiterStore:
            pass

        runner._rate_limiter.store = _NoLimiterStore()

        transport = DeterministicTelegramTransport(bot_token="token")
        _patch_transport(runner, transport)

        for i in range(3):
            transport.inject_message(
                chat_id=123,
                user_id=456,
                text=f"d-msg-{i}",
                message_id=400 + i,
            )

        runner.run_once()

        events = _audit_events(runner)
        rl_events = [
            ev for ev in events if ev["event_type"] == "cp.rate_limit.exceeded"
        ]
        assert rl_events == [], events

        store = runner._store
        rows = _outbox_rows(store)
        assert len(rows) == 3, rows
    finally:
        _close_runtime(runtime)


@pytest.mark.parametrize(
    "field, expected_default",
    [
        ("rate_limit_chat_window_s", 60),
        ("rate_limit_chat_limit", 30),
        ("rate_limit_user_window_s", 60),
        ("rate_limit_user_limit", 30),
        ("rate_limit_session_window_s", 60),
        ("rate_limit_session_limit", 40),
    ],
)
def test_rate_limit_config_defaults_match_policy(
    tmp_path: Path, field: str, expected_default: int
) -> None:
    from openminion.modules.controlplane.config import ControlPlaneConfig
    from openminion.modules.controlplane.runtime.rate_limit import RateLimitPolicy

    cfg = ControlPlaneConfig()
    assert getattr(cfg, field) == expected_default
    policy_field = field.removeprefix("rate_limit_")
    assert getattr(RateLimitPolicy(), policy_field) == expected_default
