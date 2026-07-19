from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from openminion.modules.controlplane.channels.telegram.config import (
    TelegramChannelConfig,
)
from openminion.modules.controlplane.channels.telegram.normalization import (
    session_scope_key,
)
from openminion.modules.controlplane.config import ControlPlaneConfig


def build_telegram_status_payload(
    *,
    telegram_config: TelegramChannelConfig,
    controlplane_config: ControlPlaneConfig,
    daemon_probe_status: str,
    daemon_payload: dict[str, Any],
    active_pairings: int,
    chat_id: int | None,
    topic_id: int | None,
) -> dict[str, Any]:
    channel_runtime = _extract_channel_runtime(daemon_payload)
    telegram_channel = _extract_channel_status(channel_runtime, "telegram")
    return {
        "telegram": {
            "enabled": bool(telegram_config.enabled),
            "mode": telegram_config.mode,
            "poll_state": telegram_config.polling.state_sqlite_path,
            "listener_state": str(telegram_channel.get("state") or "not_observed"),
            "listener_alive": _status_value(telegram_channel.get("listener_alive")),
            "connected": _status_value(telegram_channel.get("connected")),
        },
        "controlplane": {
            "sqlite_path": controlplane_config.sqlite_path,
            "default_profile": controlplane_config.default_agent_id,
            "openminion_target": controlplane_config.openminion_target,
        },
        "pairings": {"active": active_pairings},
        "daemon": {
            "reachable": daemon_probe_status == "ok",
            "endpoint_status": daemon_probe_status,
            "state": str(channel_runtime.get("state") or "not_observed"),
        },
        "session": _telegram_bound_session_payload(
            controlplane_config,
            chat_id=chat_id,
            topic_id=topic_id,
        ),
    }


def _extract_channel_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = payload.get("channel_runtime") if isinstance(payload, dict) else None
    if isinstance(runtime, dict):
        return runtime
    return {"state": "not_observed", "channels": {}}


def _extract_channel_status(
    channel_runtime: dict[str, Any], channel_id: str
) -> dict[str, Any]:
    channels = channel_runtime.get("channels")
    if isinstance(channels, dict):
        channel_payload = channels.get(channel_id)
        if isinstance(channel_payload, dict):
            return channel_payload
    return {}


def _status_value(value: Any) -> bool | str:
    if isinstance(value, bool):
        return value
    return "not_observed"


def _telegram_bound_session_payload(
    cp_cfg: ControlPlaneConfig,
    *,
    chat_id: int | None,
    topic_id: int | None,
) -> dict[str, Any]:
    if chat_id is None:
        return {
            "chat_key": None,
            "session_id": "not_observed",
            "profile_id": "not_observed",
            "reason": "pass --chat-id to inspect active Telegram session",
        }
    chat_key = session_scope_key(chat_id=int(chat_id), topic_id=topic_id)
    path = Path(cp_cfg.sqlite_path).expanduser()
    if not path.exists():
        return {
            "chat_key": chat_key,
            "session_id": "not_found",
            "profile_id": "not_found",
            "reason": "controlplane database not found",
        }
    try:
        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT cb.session_id,
                       COALESCE(sa.agent_id, cb.active_agent_id, ?) AS profile_id
                FROM cp_chat_bindings cb
                LEFT JOIN cp_session_agents sa ON sa.session_id = cb.session_id
                WHERE cb.chat_key = ?
                LIMIT 1
                """,
                (cp_cfg.default_agent_id, chat_key),
            ).fetchone()
    except sqlite3.Error as exc:
        return {
            "chat_key": chat_key,
            "session_id": "not_observed",
            "profile_id": "not_observed",
            "reason": str(exc),
        }
    if row is None:
        return {
            "chat_key": chat_key,
            "session_id": "not_found",
            "profile_id": "not_found",
            "reason": "no active binding for chat",
        }
    return {
        "chat_key": chat_key,
        "session_id": str(row["session_id"]),
        "profile_id": str(row["profile_id"]),
        "reason": "",
    }
