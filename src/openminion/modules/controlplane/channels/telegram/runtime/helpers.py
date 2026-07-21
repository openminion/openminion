import logging
import threading
import time
from typing import Any, Callable

from openminion.modules.controlplane.constants import PRINCIPAL_BINDING_STATUS_ACTIVE
from openminion.modules.controlplane.contracts.models import DeliveryContext
from openminion.modules.controlplane.interfaces import (
    ensure_controlplane_component_compatibility,
)
from openminion.modules.controlplane.channels.telegram.models import (
    TelegramInboundEnvelope,
    TelegramReplyTarget,
)
from openminion.modules.controlplane.channels.telegram.interfaces import (
    ensure_telegram_component_compatibility,
)
from openminion.modules.controlplane.channels.telegram.normalization import (
    session_scope_key,
    to_reply_target,
)
from openminion.modules.controlplane.channels.telegram.constants import (
    ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
)


def _resolve_runtime_store(
    runtime: Any,
    *,
    required_attrs: tuple[str, ...] = (),
    required_any: tuple[str, ...] = (),
) -> Any | None:
    store = getattr(runtime, "__dict__", {}).get("store")
    if store is None:
        return None
    if any(not hasattr(store, attr) for attr in required_attrs):
        return None
    if required_any and not any(hasattr(store, attr) for attr in required_any):
        return None
    return store


def _resolve_controlplane_pairing_store(runtime: Any) -> Any | None:
    return _resolve_runtime_store(runtime, required_attrs=("upsert_pairing",))


def _resolve_controlplane_auth_store(runtime: Any) -> Any | None:
    return _resolve_runtime_store(
        runtime,
        required_any=("resolve_principal", "get_channel_subject", "get_pairing"),
    )


def _resolve_controlplane_clarify_store(runtime: Any) -> Any | None:
    return _resolve_runtime_store(
        runtime,
        required_attrs=(
            "set_pending_clarify",
            "get_pending_clarify",
            "clear_pending_clarify",
            "resolve_session",
        ),
    )


def _resolve_controlplane_clarify_session_id(
    store: Any | None,
    *,
    envelope: TelegramInboundEnvelope,
) -> str | None:
    if store is None:
        return None
    try:
        return str(
            store.resolve_session(
                f"telegram:{envelope.from_user.id}",
                session_scope_key(envelope.chat_id, envelope.topic_id),
            )
        )
    except Exception:
        return None


def _parse_chat_id(value: Any) -> int:
    if isinstance(value, int):
        return value
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing chat_id for telegram delivery target")
    if raw.startswith("telegram:"):
        parts = raw.split(":")
        if len(parts) >= 2:
            return int(parts[1])
    return int(raw)


def _parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    return int(raw)


def _resolve_reply_target(
    ctx: DeliveryContext
    | TelegramReplyTarget
    | TelegramInboundEnvelope
    | dict[str, object],
) -> TelegramReplyTarget:
    if isinstance(ctx, TelegramReplyTarget):
        return ctx
    if isinstance(ctx, TelegramInboundEnvelope):
        return to_reply_target(ctx)
    if isinstance(ctx, DeliveryContext):
        chat_id = _parse_chat_id(ctx.chat_id)
        message_id = int(ctx.reply_to or 0)
        topic_id = _parse_optional_int(ctx.thread_id)
        return TelegramReplyTarget(
            chat_id=chat_id,
            message_id=message_id,
            topic_id=topic_id,
        )
    if isinstance(ctx, dict):
        chat_id = _parse_chat_id(ctx.get("chat_id") or ctx.get("chat_key"))
        message_id = int(ctx.get("message_id") or ctx.get("reply_to") or 0)
        topic_id = _parse_optional_int(ctx.get("topic_id") or ctx.get("thread_id"))
        return TelegramReplyTarget(
            chat_id=chat_id,
            message_id=message_id,
            topic_id=topic_id,
        )
    raise TypeError(f"unsupported delivery ctx type: {type(ctx)!r}")


def _validate_component_contracts(runner: Any) -> None:
    strict_raw = (
        str(
            runner._env.get(  # noqa: SLF001
                "OPENMINION_STRICT_CONTROLPLANE_TELEGRAM_CONTRACTS", "0"
            )
        )
        .strip()
        .lower()
    )
    strict = strict_raw not in {"", "0", "false", "no", "off"}
    components = [
        ("bot_api", runner._api),  # noqa: SLF001
        ("runtime_handler", runner._runtime),  # noqa: SLF001
        ("delivery_service", runner._delivery),  # noqa: SLF001
    ]
    if runner._state_store is not None:
        components.append(("state_store", runner._state_store))
    if runner._pairing is not None:
        components.append(("pairing_service", runner._pairing))
    for component_type, component in components:
        if (
            component_type == "runtime_handler"
            and not strict
            and not hasattr(component, "contract_version")
            and hasattr(component, "handle_inbound")
        ):
            continue
        try:
            ensure_telegram_component_compatibility(
                component, component_type=component_type
            )
        except Exception as exc:
            if strict:
                raise
            runner._log.warning(  # noqa: SLF001
                "telegram contract warning (%s): %s", component_type, exc
            )
    _validate_controlplane_component(
        runner,
        strict=strict,
        component=runner._session_sink,  # noqa: SLF001
        component_type="session_event_sink",
    )
    _validate_controlplane_component(
        runner,
        strict=strict,
        component=runner._access_policy,  # noqa: SLF001
        component_type="access_policy",
    )


def _validate_controlplane_component(
    runner: Any,
    *,
    strict: bool,
    component: Any,
    component_type: str,
) -> None:
    try:
        ensure_controlplane_component_compatibility(
            component,
            component_type=component_type,
        )
    except Exception as exc:
        if strict:
            raise
        runner._log.warning(  # noqa: SLF001
            "controlplane contract warning (%s): %s", component_type, exc
        )


def _audit_event(
    runner: Any,
    event_type: str,
    *,
    outcome: str = "ok",
    severity: str = "info",
    reason: str | None = None,
    **details: object,
) -> None:
    audit_logger = runner._audit_logger  # noqa: SLF001
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
        return
    if hasattr(audit_logger, "log"):
        audit_logger.log(
            event_type,
            outcome=outcome,
            severity=severity,
            **payload,
        )


def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _deliver_sync_fallback(
    runner: Any,
    *,
    payload: dict[str, Any],
    envelope: TelegramInboundEnvelope,
) -> None:
    result = runner.deliver(payload, envelope)
    if getattr(result, "ok", True):
        runner._audit_event(  # noqa: SLF001
            "cp.delivery.sent",
            reason="delivery_ok",
            update_id=envelope.update_id,
            chat_id=str(envelope.chat_id),
            sent_count=len(getattr(result, "sent_messages", []) or []),
        )
    else:
        runner._audit_event(  # noqa: SLF001
            "cp.delivery.failed",
            outcome="failed",
            severity="error",
            reason=str(getattr(result, "error", "") or "delivery_failed"),
            update_id=envelope.update_id,
            chat_id=str(envelope.chat_id),
        )
    session_id = _as_str_or_none(payload.get("session_id"))
    for sent in result.sent_messages:
        chat_id = _as_str_or_none(sent.get("chat", {}).get("id")) or str(
            envelope.chat_id
        )
        topic_id = _as_str_or_none(sent.get("message_thread_id"))
        runner._session_sink.record_outbound(  # noqa: SLF001
            session_id=session_id,
            chat_id=chat_id,
            topic_id=topic_id,
            payload=payload,
            telegram_message=sent,
        )


def _enqueue_outbox(
    runner: Any,
    *,
    payload: dict[str, Any],
    envelope: TelegramInboundEnvelope,
) -> None:
    store = runner._store  # noqa: SLF001
    if store is None or not hasattr(store, "enqueue_outbox"):
        runner._log.debug(  # noqa: SLF001
            "outbox unavailable; falling back to synchronous deliver "
            "(channel=%s, chat_id=%s)",
            runner.channel_id,
            envelope.chat_id,
        )
        runner._deliver_sync_fallback(payload=payload, envelope=envelope)  # noqa: SLF001
        return
    thread_id = str(envelope.topic_id) if envelope.topic_id is not None else None
    reply_to = str(envelope.message_id) if envelope.message_id is not None else None
    outbox_id = store.enqueue_outbox(
        channel=runner.channel_id,
        chat_id=str(envelope.chat_id),
        payload=payload,
        thread_id=thread_id,
        reply_to=reply_to,
    )
    runner._audit_event(  # noqa: SLF001
        "cp.outbox.enqueued",
        reason="enqueued",
        outbox_id=outbox_id,
        update_id=envelope.update_id,
        chat_id=str(envelope.chat_id),
    )


def _enqueue_inbox(
    runner: Any,
    *,
    inbound: Any,
    envelope: TelegramInboundEnvelope,
) -> bool:
    store = runner._store  # noqa: SLF001
    if store is None or not hasattr(store, "enqueue_inbox"):
        return False
    thread_id = str(envelope.topic_id) if envelope.topic_id is not None else None
    reply_to = str(envelope.message_id) if envelope.message_id is not None else None
    inbox_id, inserted = store.enqueue_inbox(
        channel=runner.channel_id,
        chat_id=str(envelope.chat_id),
        channel_message_id=str(envelope.update_id),
        user_id=str(envelope.from_user.id),
        thread_id=thread_id,
        inbound_id=f"telegram:{envelope.update_id}",
        payload=_inbox_payload_from_inbound(
            inbound,
            channel=runner.channel_id,
            reply_to=reply_to,
        ),
    )
    if inserted:
        runner._audit_event(  # noqa: SLF001
            "cp.inbox.enqueued",
            reason="enqueued",
            inbox_id=inbox_id,
            update_id=envelope.update_id,
            chat_id=str(envelope.chat_id),
        )
    return True


def _inbox_payload_from_inbound(
    inbound: Any,
    *,
    channel: str,
    reply_to: str | None,
) -> dict[str, Any]:
    timestamp = getattr(inbound, "timestamp", None)
    meta = dict(getattr(inbound, "meta", {}) or {})
    meta["controlplane_rate_limit_checked"] = True
    metadata = dict(getattr(inbound, "metadata", {}) or {})
    metadata["controlplane_rate_limit_checked"] = True
    return {
        "text": str(getattr(inbound, "text", "") or ""),
        "user_key": str(getattr(inbound, "user_key", "") or ""),
        "chat_key": str(getattr(inbound, "chat_key", "") or ""),
        "channel": str(getattr(inbound, "channel", "") or channel),
        "thread_key": _as_str_or_none(getattr(inbound, "thread_key", None)),
        "chat_id": _as_str_or_none(getattr(inbound, "chat_id", None)),
        "user_id": _as_str_or_none(getattr(inbound, "user_id", None)),
        "thread_id": _as_str_or_none(getattr(inbound, "thread_id", None)),
        "timestamp": (
            timestamp.isoformat()
            if hasattr(timestamp, "isoformat")
            else _as_str_or_none(timestamp)
        ),
        "reply_to": reply_to or _as_str_or_none(getattr(inbound, "reply_to", None)),
        "metadata": metadata,
        "meta": meta,
        "auth": _auth_payload(getattr(inbound, "auth", None)),
    }


def _auth_payload(auth: Any) -> dict[str, Any] | None:
    if auth is None:
        return None
    return {
        "role": str(getattr(auth, "role", "") or ""),
        "scopes": list(getattr(auth, "scopes", ()) or ()),
        "principal_id": _as_str_or_none(getattr(auth, "principal_id", None)),
        "metadata": dict(getattr(auth, "metadata", {}) or {}),
    }


def _has_active_principal_binding(
    runner: Any, envelope: TelegramInboundEnvelope
) -> bool:
    store = runner._auth_store  # noqa: SLF001
    if store is None:
        return False
    resolve_principal = getattr(store, "resolve_principal", None)
    get_channel_subject = getattr(store, "get_channel_subject", None)
    if not callable(resolve_principal) or not callable(get_channel_subject):
        return False
    principal_id = resolve_principal(
        channel="telegram", subject_id=str(envelope.chat_id)
    )
    if not principal_id:
        return False
    binding = get_channel_subject(channel="telegram", subject_id=str(envelope.chat_id))
    if not isinstance(binding, dict):
        return False
    status = (
        str(binding.get("status") or PRINCIPAL_BINDING_STATUS_ACTIVE).strip().lower()
    )
    return status == PRINCIPAL_BINDING_STATUS_ACTIVE


def _send_runner_online_notice(runner: Any) -> None:
    if getattr(runner, "_runner_online_notice_sent", False):
        return
    setattr(runner, "_runner_online_notice_sent", True)

    store = getattr(runner, "_store", None)
    list_pairings = getattr(store, "list_pairings", None)
    if not callable(list_pairings):
        return

    try:
        pairings = list_pairings(channel=runner.channel_id, limit=50)
    except Exception as exc:  # noqa: BLE001 - startup notice must not stop runner
        runner._log.warning(  # noqa: SLF001
            "telegram runner online notice skipped: failed to list pairings: %s",
            exc,
        )
        return

    text = (
        "OpenMinion Telegram runner is online. "
        "This computer is now listening for this chat. "
        "Use /status to check the active profile/session or /help for commands."
    )
    sent_count = 0
    for pairing in pairings:
        chat_id = _as_str_or_none(pairing.get("chat_id"))
        if chat_id is None:
            continue
        try:
            runner._delivery.send_text(  # noqa: SLF001
                text=text,
                target=TelegramReplyTarget(
                    chat_id=_parse_chat_id(chat_id),
                    message_id=0,
                ),
            )
            sent_count += 1
            runner._audit_event(  # noqa: SLF001
                "cp.telegram.runner.online_notice_sent",
                reason="runner_online",
                chat_id=chat_id,
                paired_chat_count=len(pairings),
                sent_count=sent_count,
            )
        except Exception as exc:  # noqa: BLE001 - keep listener online
            runner._log.warning(  # noqa: SLF001
                "telegram runner online notice failed chat_id=%s: %s",
                chat_id,
                exc,
            )


class _TelegramChatActionPulse:
    def __init__(
        self,
        *,
        runner: Any,
        envelope: TelegramInboundEnvelope,
        action: str = "typing",
        interval_seconds: float = 4.0,
        progress_threshold_seconds: float | None = 30.0,
    ) -> None:
        self._runner = runner
        self._envelope = envelope
        self._action = action
        self._interval_seconds = max(0.05, float(interval_seconds))
        self._progress_threshold_seconds = (
            None
            if progress_threshold_seconds is None
            else max(0.0, float(progress_threshold_seconds))
        )
        self._progress_sent = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_TelegramChatActionPulse":
        self._send_once()
        thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"telegram-chat-action-{self._envelope.chat_id}",
        )
        self._thread = thread
        thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.2)
            self._thread = None

    def _run(self) -> None:
        started_at = time.monotonic()
        while not self._stop.wait(self._interval_seconds):
            self._send_once()
            threshold = self._progress_threshold_seconds
            if (
                threshold is not None
                and not self._progress_sent
                and time.monotonic() - started_at >= threshold
            ):
                self._send_progress_notice()

    def _send_once(self) -> None:
        payload: dict[str, Any] = {
            "chat_id": self._envelope.chat_id,
            "action": self._action,
        }
        if self._envelope.topic_id is not None:
            payload["message_thread_id"] = self._envelope.topic_id
        try:
            api = self._runner._api  # noqa: SLF001
            sender = getattr(api, "send_chat_action", None)
            if callable(sender):
                sender(payload)
                return
            caller = getattr(api, "call", None)
            if callable(caller):
                caller("sendChatAction", payload)
        except Exception as exc:  # noqa: BLE001 - typing indicator is best-effort
            self._runner._log.debug(  # noqa: SLF001
                "telegram chat action failed chat_id=%s action=%s: %s",
                self._envelope.chat_id,
                self._action,
                exc,
            )

    def _send_progress_notice(self) -> None:
        self._progress_sent = True
        try:
            self._runner._delivery.send_text(  # noqa: SLF001
                text="Still working on it. OpenMinion will reply here when the turn finishes.",
                target=TelegramReplyTarget(
                    chat_id=self._envelope.chat_id,
                    message_id=0,
                    topic_id=self._envelope.topic_id,
                ),
            )
            self._runner._audit_event(  # noqa: SLF001
                "cp.telegram.turn.progress_notice_sent",
                reason="turn_still_running",
                chat_id=str(self._envelope.chat_id),
                update_id=self._envelope.update_id,
            )
        except Exception as exc:  # noqa: BLE001 - progress notice is best-effort
            self._runner._log.debug(  # noqa: SLF001
                "telegram progress notice failed chat_id=%s: %s",
                self._envelope.chat_id,
                exc,
            )


def _chat_action_pulse(
    runner: Any,
    *,
    envelope: TelegramInboundEnvelope,
) -> _TelegramChatActionPulse:
    interval = getattr(runner, "_chat_action_interval_seconds", 4.0)
    threshold = getattr(runner, "_progress_notice_after_seconds", 30.0)
    return _TelegramChatActionPulse(
        runner=runner,
        envelope=envelope,
        interval_seconds=interval,
        progress_threshold_seconds=threshold,
    )


def _dispatch_runtime_with_parity_error(
    *,
    runtime: Any,
    inbound: Any,
    envelope: TelegramInboundEnvelope,
    audit_event: Callable[..., None],
    logger: logging.Logger,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run the runtime dispatch and normalize channel-surface failures."""

    try:
        return runtime.handle_inbound(inbound), None
    except Exception as exc:  # noqa: BLE001 - normalize channel failure facts
        audit_event(
            "cp.route.runtime_failed",
            outcome="failed",
            severity="error",
            reason=ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
            update_id=envelope.update_id,
            chat_id=str(envelope.chat_id),
            error_code=ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
            error_type=type(exc).__name__,
        )
        logger.exception(
            "runtime dispatch failed update_id=%s chat_id=%s: %s",
            envelope.update_id,
            envelope.chat_id,
            exc,
        )
        return None, {
            "success": False,
            "error": str(exc),
            "error_code": ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
            "reason": ROUTE_REASON_RUNTIME_DISPATCH_FAILED,
            "update_id": envelope.update_id,
        }
