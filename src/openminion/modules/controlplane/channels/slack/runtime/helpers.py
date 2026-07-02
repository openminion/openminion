"""Shared Slack runner helpers."""

from __future__ import annotations

import logging
from typing import Any

from openminion.modules.controlplane.channels.slack.access import SlackAccessPolicy
from openminion.modules.controlplane.channels.slack.constants import (
    CHANNEL_ID,
    ROUTE_REASON_ACCESS_DENIED,
    ROUTE_REASON_DUPLICATE_EVENT,
    ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
)
from openminion.modules.controlplane.channels.slack.models import SlackInboundEnvelope
from openminion.modules.controlplane.channels.slack.normalization import (
    inbound_from_envelope,
    to_reply_target,
)


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
    log: logging.Logger = runner._log  # noqa: SLF001
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
    inbound = inbound_from_envelope(envelope)
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
            event_id=envelope.event_id,
        )
        raise
    enqueue_or_deliver(runner, payload=payload, envelope=envelope)
    return payload


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
