"""Shared Slack runner helpers."""

from __future__ import annotations

import logging
from dataclasses import replace
from hashlib import sha256
from typing import Any

from openminion.modules.controlplane.channels.slack.access import (
    SlackAccessDecision,
    SlackAccessPolicy,
)
from openminion.modules.controlplane.channels.slack.constants import (
    CHANNEL_ID,
    ROUTE_REASON_ACCESS_DENIED,
    ROUTE_REASON_DUPLICATE_EVENT,
    ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
)
from openminion.modules.controlplane.channels.slack.models import (
    SlackInboundEnvelope,
    SlackSlashCommandEnvelope,
)
from openminion.modules.controlplane.channels.slack.normalization import (
    inbound_from_envelope,
    to_reply_target,
)
from openminion.modules.controlplane.channels.slack.slash_commands import (
    inbound_from_slash,
)
from openminion.modules.controlplane.contracts.models import AuthContext


def audit_event(
    audit_logger: Any | None,
    event_type: str,
    *,
    outcome: str = "ok",
    severity: str = "info",
    reason: str | None = None,
    **details: object,
) -> None:
    if audit_logger is None:
        return
    payload = dict(details)
    if reason is not None:
        payload["reason"] = reason
    if hasattr(audit_logger, "emit"):
        audit_logger.emit(
            event_type,
            outcome=outcome,
            severity=severity,
            details=payload,
        )


def process_envelope(
    runner: Any, envelope: SlackInboundEnvelope
) -> dict[str, Any] | None:
    state_store = runner._state_store  # noqa: SLF001
    audit_logger = runner._audit_logger  # noqa: SLF001
    if state_store is not None and not state_store.mark_event_seen(envelope.event_id):
        audit_event(
            audit_logger,
            "cp.slack.event.duplicate",
            reason=ROUTE_REASON_DUPLICATE_EVENT,
            event_id=envelope.event_id,
        )
        return None
    access = SlackAccessPolicy(runner._config.access).evaluate(envelope)  # noqa: SLF001
    if not access.allowed:
        audit_event(
            audit_logger,
            "cp.access.deny",
            outcome="denied",
            severity="warning",
            reason=ROUTE_REASON_ACCESS_DENIED,
            slack_reason=access.reason,
            channel=CHANNEL_ID,
            chat_id=envelope.channel_id,
        )
        return {"ok": False, "reason": access.reason}
    inbound = _with_access_auth(runner, inbound_from_envelope(envelope))
    if enqueue_inbound_or_dispatch(
        runner,
        inbound=inbound,
        chat_id=envelope.channel_id,
        channel_message_id=envelope.event_id,
        user_id=envelope.user_id,
        thread_id=envelope.thread_ts,
        reply_to=envelope.ts,
    ):
        return {"ok": True, "status": "enqueued"}
    return dispatch_and_deliver(runner, inbound=inbound, envelope=envelope)


def process_slash_command(
    runner: Any, envelope: SlackSlashCommandEnvelope
) -> dict[str, Any] | None:
    audit_logger = runner._audit_logger  # noqa: SLF001
    access = _evaluate_slash_access(runner, envelope)
    if not access.allowed:
        audit_event(
            audit_logger,
            "cp.access.deny",
            outcome="denied",
            severity="warning",
            reason=ROUTE_REASON_ACCESS_DENIED,
            slack_reason=access.reason,
            channel=CHANNEL_ID,
            chat_id=envelope.channel_id,
        )
        return {"ok": False, "reason": access.reason}
    inbound = _with_access_auth(runner, inbound_from_slash(envelope))
    message_id = _slash_message_id(envelope)
    if enqueue_inbound_or_dispatch(
        runner,
        inbound=inbound,
        chat_id=envelope.channel_id,
        channel_message_id=message_id,
        user_id=envelope.user_id,
        thread_id=None,
        reply_to=None,
    ):
        return {"ok": True, "status": "enqueued"}
    return dispatch_and_deliver(
        runner,
        inbound=inbound,
        slash_channel_id=envelope.channel_id,
    )


def dispatch_and_deliver(
    runner: Any,
    *,
    inbound: Any,
    envelope: SlackInboundEnvelope | None = None,
    slash_channel_id: str | None = None,
) -> dict[str, Any] | None:
    log: logging.Logger = runner._log  # noqa: SLF001
    audit_logger = runner._audit_logger  # noqa: SLF001
    try:
        payload = runner._runtime.handle_inbound(inbound)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        log.warning("slack runtime dispatch failed: %s", exc, exc_info=True)
        audit_event(
            audit_logger,
            "cp.slack.dispatch.failed",
            outcome="error",
            severity="error",
            reason=ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
            event_id=envelope.event_id if envelope is not None else None,
        )
        raise
    if envelope is not None:
        enqueue_or_deliver(runner, payload=payload, envelope=envelope)
    elif slash_channel_id is not None and isinstance(payload, dict):
        runner._delivery.deliver(  # noqa: SLF001
            payload,
            {
                "channel_id": slash_channel_id,
            },
        )
    return payload


def enqueue_inbound_or_dispatch(
    runner: Any,
    *,
    inbound: Any,
    chat_id: str,
    channel_message_id: str,
    user_id: str,
    thread_id: str | None,
    reply_to: str | None,
) -> bool:
    store = runner._store  # noqa: SLF001
    if store is None or not hasattr(store, "enqueue_inbox"):
        return False
    inbox_id, inserted = store.enqueue_inbox(
        channel=CHANNEL_ID,
        chat_id=chat_id,
        channel_message_id=channel_message_id,
        user_id=user_id,
        thread_id=thread_id,
        inbound_id=f"{CHANNEL_ID}:{channel_message_id}",
        payload=_inbox_payload_from_inbound(
            inbound,
            reply_to=reply_to,
        ),
    )
    if inserted:
        audit_event(
            runner._audit_logger,  # noqa: SLF001
            "cp.inbox.enqueued",
            inbox_id=inbox_id,
            channel=CHANNEL_ID,
            chat_id=chat_id,
            thread_id=thread_id,
        )
    return True


def enqueue_or_deliver(
    runner: Any, *, payload: dict[str, Any], envelope: SlackInboundEnvelope
) -> None:
    store = runner._store  # noqa: SLF001
    if store is None or not hasattr(store, "enqueue_outbox"):
        runner._delivery.deliver(payload, to_reply_target(envelope))  # noqa: SLF001
        return
    outbox_id = store.enqueue_outbox(
        channel=CHANNEL_ID,
        chat_id=envelope.channel_id,
        payload=payload,
        thread_id=envelope.thread_ts,
        reply_to=envelope.ts,
    )
    audit_event(
        runner._audit_logger,  # noqa: SLF001
        "cp.outbox.enqueued",
        outbox_id=outbox_id,
        channel=CHANNEL_ID,
        chat_id=envelope.channel_id,
        thread_id=envelope.thread_ts,
    )


def _inbox_payload_from_inbound(
    inbound: Any,
    *,
    reply_to: str | None,
) -> dict[str, Any]:
    timestamp = getattr(inbound, "timestamp", None)
    return {
        "text": str(getattr(inbound, "text", "") or ""),
        "user_key": str(getattr(inbound, "user_key", "") or ""),
        "chat_key": str(getattr(inbound, "chat_key", "") or ""),
        "channel": str(getattr(inbound, "channel", "") or CHANNEL_ID),
        "thread_key": getattr(inbound, "thread_key", None),
        "chat_id": getattr(inbound, "chat_id", None),
        "user_id": getattr(inbound, "user_id", None),
        "thread_id": getattr(inbound, "thread_id", None),
        "timestamp": (
            timestamp.isoformat()
            if hasattr(timestamp, "isoformat")
            else str(timestamp or "")
        ),
        "reply_to": reply_to,
        "metadata": dict(getattr(inbound, "metadata", {}) or {}),
        "meta": dict(getattr(inbound, "meta", {}) or {}),
        "auth": _auth_payload(getattr(inbound, "auth", None)),
    }


def _auth_payload(auth: Any) -> dict[str, Any] | None:
    if auth is None:
        return None
    return {
        "role": str(getattr(auth, "role", "") or ""),
        "scopes": list(getattr(auth, "scopes", ()) or ()),
        "principal_id": getattr(auth, "principal_id", None),
        "metadata": dict(getattr(auth, "metadata", {}) or {}),
    }


def _with_access_auth(runner: Any, inbound: Any) -> Any:
    config = runner._config  # noqa: SLF001
    if config.access.require_pairing:
        return inbound
    return replace(
        inbound,
        auth=AuthContext(
            role="channel_allowed",
            scopes=tuple(config.pairing.default_scopes),
        ),
    )


def _evaluate_slash_access(
    runner: Any, envelope: SlackSlashCommandEnvelope
) -> SlackAccessDecision:
    config = runner._config.access  # noqa: SLF001
    if config.allowed_team_ids and envelope.team_id not in config.allowed_team_ids:
        return SlackAccessDecision(False, "team_allowlist_miss")
    if (
        config.allowed_channel_ids
        and envelope.channel_id not in config.allowed_channel_ids
    ):
        return SlackAccessDecision(False, "channel_allowlist_miss")
    return SlackAccessDecision(True)


def _slash_message_id(envelope: SlackSlashCommandEnvelope) -> str:
    seed = "|".join(
        (
            envelope.team_id,
            envelope.channel_id,
            envelope.user_id,
            envelope.command,
            envelope.text,
            str(envelope.trigger_id or ""),
            str(envelope.response_url or ""),
        )
    )
    return "slash:" + sha256(seed.encode("utf-8")).hexdigest()[:32]
