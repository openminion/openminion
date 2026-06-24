from collections import OrderedDict
from dataclasses import replace
import hmac
import logging
import threading
import time
from typing import Any

from openminion.modules.controlplane.interfaces import (
    AccessPolicyAPI,
    SessionEventSinkAPI,
)
from openminion.modules.controlplane.constants import (
    AUTH_ROLE_UNPAIRED,
)
from openminion.modules.controlplane.contracts.models import DeliveryContext
from openminion.modules.controlplane.channels.telegram.access import (
    TelegramAccessPolicy,
)
from openminion.modules.controlplane.runtime.parser import SlashCommandParser
from openminion.modules.controlplane.runtime.security import (
    ScopeAuthorizer,
    is_pair_command,
)
from openminion.modules.controlplane.channels.telegram.bot_api import TelegramBotAPI
from openminion.modules.controlplane.channels.telegram.clarify import (
    extract_clarify_request,
    parse_clarify_answer,
    render_clarify_prompt,
)
from openminion.modules.controlplane.channels.telegram.command_aliases import (
    normalize_command_aliases,
)
from openminion.modules.controlplane.channels.telegram.config import (
    TelegramChannelConfig,
)
from openminion.modules.controlplane.channels.telegram.constants import (
    ACCESS_REASON_DM_ALLOWLIST_MISS,
    ACCESS_REASON_GROUP_ALLOWLIST_MISS,
    ACCESS_REASON_GROUP_POLICY_DENY,
    ACCESS_REASON_PAIRED_BINDING,
    PAIRING_MODE_OFF,
    ROUTE_REASON_RUNTIME_DISPATCH,
)
from openminion.modules.controlplane.channels.telegram.delivery import (
    TelegramDeliveryService,
)
from openminion.modules.controlplane.channels.telegram.interfaces import (
    TELEGRAM_INTERFACE_VERSION,
)
from openminion.modules.controlplane.channels.telegram.models import (
    TelegramInboundEnvelope,
    TelegramReplyTarget,
)
from openminion.modules.controlplane.channels.telegram.normalization import (
    extract_envelope,
    to_control_event,
    to_inbound_message,
    to_reply_target,
)
from openminion.modules.controlplane.channels.telegram.pairing import (
    TelegramPairingService,
)
from openminion.modules.controlplane.channels.telegram.events import (
    NoopSessionEventSink,
)
from openminion.modules.controlplane.channels.telegram.listener import (
    WebhookHTTPListener,
    build_listener,
)
from openminion.modules.controlplane.channels.telegram.runtime.helpers import (
    _audit_event as _runtime_audit_event,
    _deliver_sync_fallback as _runtime_deliver_sync_fallback,
    _dispatch_runtime_with_parity_error,
    _enqueue_outbox as _runtime_enqueue_outbox,
    _has_active_principal_binding as _runtime_has_active_principal_binding,
    _resolve_controlplane_auth_store,
    _resolve_controlplane_clarify_session_id,
    _resolve_controlplane_clarify_store,
    _resolve_controlplane_pairing_store,
    _resolve_reply_target,
    _validate_component_contracts as _runtime_validate_component_contracts,
)
from openminion.base.config.env import resolve_environment_config

_LOG = logging.getLogger(__name__)


class TelegramWebhookRunner:
    """TGIT-02: Webhook runtime for processing Telegram updates via webhook.

    Reuses the shared update normalization/dispatch pipeline from TelegramPollingRunner.
    """

    contract_version = TELEGRAM_INTERFACE_VERSION
    channel_id = "telegram"

    def __init__(
        self,
        *,
        config: TelegramChannelConfig,
        api: TelegramBotAPI,
        runtime: Any,
        delivery: TelegramDeliveryService,
        state_store: Any = None,
        session_sink: SessionEventSinkAPI | None = None,
        pairing_service: TelegramPairingService | None = None,
        access_policy: AccessPolicyAPI | None = None,
        audit_logger: object | None = None,
        account_id: str | None = None,
        logger: logging.Logger | None = None,
        env: dict[str, str] | None = None,
        store: object | None = None,
        outbox_worker: object | None = None,
        rate_limiter: object | None = None,
        brain_client: object | None = None,
    ) -> None:
        self._config = config
        self._api = api
        self._runtime = runtime
        self._delivery = delivery
        self._state_store = state_store
        self._session_sink = session_sink or NoopSessionEventSink()
        self._log = logger or _LOG
        self._env = (
            dict(env) if env is not None else resolve_environment_config().snapshot()
        )
        self._pairing = pairing_service
        self._audit_logger = audit_logger
        self._store = store if store is not None else getattr(runtime, "store", None)
        self._outbox_worker = outbox_worker
        self._outbox_thread: threading.Thread | None = None
        self._outbox_stop_event: threading.Event | None = None
        self._rate_limiter = rate_limiter
        self._brain_client = brain_client
        if self._pairing is None and self._state_store is not None:
            self._pairing = TelegramPairingService(
                config=self._config.pairing,
                store=self._state_store,
                controlplane_store=_resolve_controlplane_pairing_store(self._runtime),
                logger=self._log,
            )
        self._auth_store = _resolve_controlplane_auth_store(self._runtime)
        self._authorizer = (
            ScopeAuthorizer(store=self._auth_store)
            if self._auth_store is not None
            else None
        )
        self._access_policy = access_policy or TelegramAccessPolicy(
            access=self._config.access
        )
        self._clarify_store = _resolve_controlplane_clarify_store(self._runtime)
        self._command_parser = SlashCommandParser()

        self._account_id = account_id or "default"
        self._bot_username: str | None = None
        self._http_listener: WebhookHTTPListener | None = None
        self._recent_update_ids: OrderedDict[int, None] = OrderedDict()
        self._update_id_lock = threading.Lock()
        self._last_request_ts: float | None = None
        self._last_error: str | None = None
        self._validate_component_contracts()

    def _validate_component_contracts(self) -> None:
        _runtime_validate_component_contracts(self)

    def initialize(self) -> None:
        """Initialize the webhook runner by fetching bot info."""
        if self._bot_username is not None:
            return

        me = self._api.get_me()
        self._bot_username = _as_str_or_none(me.get("username"))
        bot_id = _as_str_or_none(me.get("id"))
        if bot_id:
            self._account_id = f"telegram-bot:{bot_id}"

    def start(self, stop_event: threading.Event | None = None) -> None:
        self.initialize()
        self._start_outbox_worker(stop_event)
        self._start_http_listener()
        if stop_event is None:
            return
        try:
            while not stop_event.is_set():
                time.sleep(0.1)
        finally:
            self._stop_http_listener()
            self._stop_outbox_worker()

    def stop(self) -> None:
        if self._outbox_stop_event is not None:
            self._outbox_stop_event.set()
        self._stop_http_listener()
        self._stop_outbox_worker()
        self._close_brain_client()

    def _start_http_listener(self) -> None:
        if self._http_listener is not None:
            return
        listener = build_listener(config=self._config, runner=self, logger=self._log)
        if listener is None:
            return
        self._http_listener = listener
        listener.start()

    def _stop_http_listener(self) -> None:
        listener = self._http_listener
        if listener is None:
            return
        try:
            listener.stop()
        finally:
            self._http_listener = None

    @property
    def http_listener(self) -> WebhookHTTPListener | None:
        """WHS-02: expose the bound listener so integration tests can read the port."""
        return self._http_listener

    def _start_outbox_worker(self, stop_event: threading.Event | None) -> None:
        if self._outbox_worker is None:
            return
        if self._outbox_thread is not None and self._outbox_thread.is_alive():
            return
        if stop_event is None:
            stop_event = threading.Event()
        self._outbox_stop_event = stop_event
        thread = threading.Thread(
            target=self._run_outbox_loop,
            args=(stop_event,),
            daemon=True,
            name=f"outbox-worker-{self.channel_id}",
        )
        self._outbox_thread = thread
        thread.start()

    def _stop_outbox_worker(self) -> None:
        thread = self._outbox_thread
        if thread is None:
            return
        thread.join(timeout=5.0)
        if thread.is_alive():
            self._log.warning(
                "outbox worker thread did not join within timeout (channel=%s)",
                self.channel_id,
            )
        self._outbox_thread = None
        self._outbox_stop_event = None

    def _run_outbox_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                result = self._outbox_worker.run_once()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                self._log.exception("outbox worker run_once failed: %s", exc)
                time.sleep(0.1)
                continue
            if result is None:
                time.sleep(0.1)

    def deliver(
        self,
        payload: dict[str, Any],
        ctx: DeliveryContext
        | TelegramReplyTarget
        | TelegramInboundEnvelope
        | dict[str, object],
    ) -> Any:
        target = _resolve_reply_target(ctx)
        return self._delivery.send_payload(payload, target)

    def handle_webhook_update(
        self,
        update: dict[str, Any],
        secret_token: object | None = None,
    ) -> dict[str, Any]:
        """TGIT-02, TGIT-03: Handle incoming webhook update with secret verification."""
        from openminion.modules.controlplane.runtime.gate import (
            TELEGRAM_INGRESS_REQUIRED_MODULES,
            assert_controlplane_lane,
        )

        assert_controlplane_lane(
            ingress="telegram_webhook",
            required_modules=TELEGRAM_INGRESS_REQUIRED_MODULES,
        )

        self._last_request_ts = time.time()
        self._last_error = None

        try:
            configured_secret = str(self._config.webhook.secret or "").strip()
            if configured_secret:
                normalized_secret, secret_error = self._normalize_secret_token(
                    secret_token
                )
                if secret_error == "missing_secret_token":
                    self._last_error = "missing secret token"
                    self._log.warning("webhook request missing secret token")
                    return {
                        "success": False,
                        "error": "unauthorized",
                        "reason": "missing_secret_token",
                    }

                if secret_error is not None:
                    self._last_error = secret_error
                    self._log.warning(
                        "webhook request secret token normalization failed: %s",
                        secret_error,
                    )
                    return {
                        "success": False,
                        "error": "unauthorized",
                        "reason": secret_error,
                    }

                if not self._verify_secret(normalized_secret):
                    self._last_error = "invalid secret token"
                    self._log.warning("webhook request failed secret verification")
                    return {
                        "success": False,
                        "error": "unauthorized",
                        "reason": "invalid_secret_token",
                    }

            self.initialize()

            update_id = update.get("update_id")
            if update_id is not None:
                with self._update_id_lock:
                    if update_id in self._recent_update_ids:
                        self._log.info("duplicate update_id=%s, skipping", update_id)
                        return {
                            "success": True,
                            "duplicate": True,
                            "update_id": update_id,
                        }
                    self._recent_update_ids[update_id] = None
                    if len(self._recent_update_ids) > 1000:
                        for _ in range(500):
                            self._recent_update_ids.popitem(last=False)

            dispatch_error = self._process_update(update)
            if dispatch_error is not None:
                self._last_error = str(dispatch_error.get("error") or "")
                return dispatch_error
            return {"success": True, "update_id": update_id}

        except Exception as exc:
            self._last_error = str(exc)
            self._log.exception("webhook update processing failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def _normalize_secret_token(
        self, secret_token: object | None
    ) -> tuple[str | None, str | None]:
        if secret_token is None:
            return None, "missing_secret_token"
        if isinstance(secret_token, bytes):
            try:
                return secret_token.decode("utf-8"), None
            except UnicodeDecodeError:
                return None, "invalid_secret_token_encoding"
        if isinstance(secret_token, str):
            return secret_token, None
        return None, "invalid_secret_token_type"

    def _verify_secret(self, secret_token: str | None) -> bool:
        """TGIT-03: Verify the webhook secret token."""
        if not self._config.webhook.secret:
            return True
        if secret_token is None:
            return False

        expected = self._config.webhook.secret
        return hmac.compare_digest(secret_token, expected)

    def _process_update(self, update: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single update using the shared pipeline."""
        envelope = extract_envelope(update)
        if envelope is None:
            raise ValueError("unsupported or malformed telegram update")

        control_event = to_control_event(envelope)
        self._session_sink.record_inbound(control_event, update)

        if self._pairing is not None:
            pairing_result = self._pairing.handle_start_pairing(
                envelope,
                bot_username=self._bot_username,
            )
            if pairing_result.handled:
                self._audit_event(
                    "cp.route.pairing_handled",
                    reason="pairing_start",
                    update_id=envelope.update_id,
                    chat_id=str(envelope.chat_id),
                )
                if pairing_result.reply_text and self._config.actions.send_message:
                    self._send_local_text(
                        envelope,
                        pairing_result.reply_text,
                        payload_type="pairing",
                    )
                return None

        access = self._access_policy.evaluate(
            envelope,
            bot_username=self._bot_username,
        )
        if (
            not access.allowed
            and access.reason
            in {
                ACCESS_REASON_DM_ALLOWLIST_MISS,
                ACCESS_REASON_GROUP_POLICY_DENY,
                ACCESS_REASON_GROUP_ALLOWLIST_MISS,
            }
            and self._has_active_principal_binding(envelope)
        ):
            access = type(access)(allowed=True, reason=ACCESS_REASON_PAIRED_BINDING)
        if access.allowed:
            self._audit_event(
                "cp.access.allow",
                reason=access.reason,
                update_id=envelope.update_id,
                chat_id=str(envelope.chat_id),
            )
        if not access.allowed:
            self._audit_event(
                "cp.access.deny",
                outcome="denied",
                severity="warning",
                reason=access.reason,
                update_id=envelope.update_id,
                chat_id=str(envelope.chat_id),
            )
            self._maybe_send_pairing_hint(envelope, reason=access.reason)
            self._log.info(
                "dropping telegram update_id=%s reason=%s",
                envelope.update_id,
                access.reason,
            )
            self._answer_callback_if_needed(envelope)
            return None

        normalized_text = normalize_command_aliases(
            envelope.text,
            bot_username=self._bot_username,
        )
        if self._handle_local_command(envelope, normalized_text):
            self._audit_event(
                "cp.route.local_command",
                reason="local_command",
                update_id=envelope.update_id,
                chat_id=str(envelope.chat_id),
            )
            self._answer_callback_if_needed(envelope)
            return None

        inbound = to_inbound_message(
            envelope,
            normalized_text=normalized_text,
            control_event=control_event,
            extra_meta={},
        )

        clarify_session_id = _resolve_controlplane_clarify_session_id(
            self._clarify_store,
            envelope=envelope,
        )
        pending_clarify = self._load_pending_clarify(
            envelope=envelope,
            session_id=clarify_session_id,
        )
        extra_meta: dict[str, Any] = {}
        parsed_answer = (
            parse_clarify_answer(
                text=normalized_text,
                pending=pending_clarify,
                answer_prefix=self._config.clarify.answer_prefix,
            )
            if self._config.clarify.enabled
            else None
        )
        if parsed_answer is not None:
            if pending_clarify is not None:
                extra_meta["trace_id"] = str(
                    pending_clarify.get("trace_id", "")
                ).strip()
            extra_meta["clarify_answer"] = parsed_answer
            inbound = to_inbound_message(
                envelope,
                normalized_text=normalized_text,
                control_event=control_event,
                extra_meta=extra_meta,
            )

        inbound = self._apply_scope_auth_gate(inbound=inbound, envelope=envelope)
        if inbound is None:
            self._answer_callback_if_needed(envelope)
            return None

        # rate limiter gate (parallel to polling.py).
        if self._rate_limiter is not None and self._is_rate_limited(
            inbound=inbound, envelope=envelope
        ):
            return None

        self._audit_event(
            "cp.route.runtime_dispatch",
            reason=ROUTE_REASON_RUNTIME_DISPATCH,
            update_id=envelope.update_id,
            chat_id=str(envelope.chat_id),
        )
        payload, dispatch_error = _dispatch_runtime_with_parity_error(
            runtime=self._runtime,
            inbound=inbound,
            envelope=envelope,
            audit_event=self._audit_event,
            logger=self._log,
        )
        if dispatch_error is not None:
            self._answer_callback_if_needed(envelope)
            return dispatch_error
        assert payload is not None
        if self._config.clarify.enabled:
            clarify_request = extract_clarify_request(payload)
            if clarify_request is not None:
                payload_data = payload.get("data")
                payload_trace = (
                    payload_data.get("trace_id", "")
                    if isinstance(payload_data, dict)
                    else ""
                )
                self._store_pending_clarify(
                    envelope=envelope,
                    session_id=clarify_session_id,
                    clarify_payload={
                        "clarify_id": str(
                            clarify_request.get("clarify_id", "")
                        ).strip(),
                        "session_id": str(
                            payload.get("session_id")
                            or clarify_request.get("session_id")
                            or ""
                        ).strip(),
                        "trace_id": str(
                            clarify_request.get("trace_id") or payload_trace or ""
                        ).strip(),
                        "questions": list(clarify_request.get("questions", [])),
                    },
                )
                payload = dict(payload)
                payload["clarify"] = clarify_request
                payload["text"] = render_clarify_prompt(
                    clarify_request,
                    max_questions=self._config.clarify.max_questions_per_message,
                    answer_prefix=self._config.clarify.answer_prefix,
                )
            elif (
                pending_clarify is not None
                and str(payload.get("status", "")).strip().lower() != "waiting_user"
            ):
                self._clear_pending_clarify(
                    envelope=envelope,
                    session_id=clarify_session_id,
                )
        self._answer_callback_if_needed(envelope)

        if not self._config.actions.send_message:
            self._audit_event(
                "cp.delivery.skipped",
                reason="send_message_disabled",
                update_id=envelope.update_id,
                chat_id=str(envelope.chat_id),
            )
            return None

        # enqueue to durable outbox; OutboxWorker drains
        # asynchronously and emits cp.delivery.sent / cp.delivery.failed.
        self._enqueue_outbox(payload=payload, envelope=envelope)
        return None

    def _handle_local_command(
        self, envelope: TelegramInboundEnvelope, text: str
    ) -> bool:
        if not text or not text.strip().startswith("/"):
            return False

        stripped = text.strip()
        cmd = stripped.split()[0].lower()

        if cmd == "/status":
            self._send_local_text(
                envelope,
                f"🤖 Telegram Webhook Mode Active\nAccount: {self._account_id}\nBot: @{self._bot_username or 'unknown'}",
            )
            return True

        if cmd == "/exit":
            self._send_local_text(
                envelope,
                "ℹ️ Exit command not supported in webhook mode. Use your hosting platform to disable the webhook.",
            )
            return True

        return False

    def _maybe_send_pairing_hint(
        self, envelope: TelegramInboundEnvelope, reason: str
    ) -> None:
        """Send a pairing hint if access was denied."""
        if self._pairing is None:
            return
        if envelope.chat_type != "private":
            return
        if envelope.raw_type != "message":
            return
        if reason != ACCESS_REASON_DM_ALLOWLIST_MISS:
            return
        if (
            not self._config.pairing.enabled
            or self._config.pairing.mode == PAIRING_MODE_OFF
        ):
            return

        self._send_local_text(
            envelope,
            "Pairing required. Ask the owner for a fresh /start token.",
        )

    def _answer_callback_if_needed(self, envelope: TelegramInboundEnvelope) -> None:
        """Answer callback query if present."""
        if envelope.callback_query_id:
            try:
                self._api.answer_callback_query(envelope.callback_query_id)
            except Exception:
                self._log.debug(
                    "failed to answer callback_query_id=%s", envelope.callback_query_id
                )

    def _deliver_sync_fallback(
        self,
        *,
        payload: dict[str, Any],
        envelope: TelegramInboundEnvelope,
    ) -> None:
        _runtime_deliver_sync_fallback(self, payload=payload, envelope=envelope)

    def _enqueue_outbox(
        self,
        *,
        payload: dict[str, Any],
        envelope: TelegramInboundEnvelope,
    ) -> None:
        _runtime_enqueue_outbox(self, payload=payload, envelope=envelope)

    def _is_rate_limited(
        self,
        *,
        inbound: Any,
        envelope: TelegramInboundEnvelope,
    ) -> bool:
        """CPD-03: see polling.py for the canonical docstring."""
        session_id = ""
        router = getattr(self._runtime, "router", None)
        if router is not None and hasattr(router, "resolve"):
            try:
                session_id = str(router.resolve(inbound).session_id or "").strip()
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "rate-limit session resolution failed: %s", exc, exc_info=True
                )
                return False
        try:
            allowed, reason = self._rate_limiter.check(  # type: ignore[union-attr]
                inbound, session_id
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "rate-limit check raised (%s); allowing message through", exc
            )
            return False
        if allowed:
            return False
        self._audit_event(
            "cp.rate_limit.exceeded",
            outcome="denied",
            severity="warning",
            reason=reason,
            session_id=session_id,
            user_key=getattr(inbound, "user_key", ""),
            chat_id=str(envelope.chat_id),
        )
        self._send_throttle_response(envelope, reason)
        self._answer_callback_if_needed(envelope)
        return True

    def _send_throttle_response(
        self,
        envelope: TelegramInboundEnvelope,
        reason: str,
    ) -> None:
        """CPD-03: short user-facing throttle reply via the delivery service."""
        if not self._config.actions.send_message:
            return
        self._send_local_text(envelope, "Rate limit exceeded — please slow down.")

    def _close_brain_client(self) -> None:
        """CPD-06: best-effort close (parallel to polling.py)."""
        brain = self._brain_client
        if brain is None or not hasattr(brain, "close"):
            return
        try:
            brain.close()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("brain_client.close failed: %s", exc, exc_info=True)

    def _audit_event(
        self,
        event_type: str,
        *,
        outcome: str = "ok",
        severity: str = "info",
        reason: str | None = None,
        **details: object,
    ) -> None:
        _runtime_audit_event(
            self,
            event_type,
            outcome=outcome,
            severity=severity,
            reason=reason,
            **details,
        )

    def _load_pending_clarify(
        self,
        *,
        envelope: TelegramInboundEnvelope,
        session_id: str | None,
    ) -> dict[str, Any] | None:
        if not self._config.clarify.enabled:
            return None
        if self._clarify_store is not None and session_id:
            payload = self._clarify_store.get_pending_clarify(session_id)
            return dict(payload) if isinstance(payload, dict) else None
        return None

    def _store_pending_clarify(
        self,
        *,
        envelope: TelegramInboundEnvelope,
        session_id: str | None,
        clarify_payload: dict[str, Any],
    ) -> None:
        if self._clarify_store is not None and session_id:
            self._clarify_store.set_pending_clarify(session_id, clarify_payload)

    def _clear_pending_clarify(
        self,
        *,
        envelope: TelegramInboundEnvelope,
        session_id: str | None,
    ) -> None:
        if self._clarify_store is not None and session_id:
            self._clarify_store.clear_pending_clarify(session_id)

    def get_debug_info(self) -> dict[str, Any]:
        """TGIT-06: Get debug info for the webhook runner."""
        return {
            "mode": "webhook",
            "account_id": self._account_id,
            "bot_username": self._bot_username,
            "webhook_configured": bool(self._config.webhook.url),
            "webhook_secret_set": bool(self._config.webhook.secret),
            "last_request_ts": self._last_request_ts,
            "last_error": self._last_error,
            "recent_update_count": len(self._recent_update_ids),
        }

    def _apply_scope_auth_gate(
        self,
        *,
        inbound: Any,
        envelope: TelegramInboundEnvelope,
    ) -> Any | None:
        if self._authorizer is None:
            return inbound

        auth = self._authorizer.auth_for_inbound(inbound)
        inbound = replace(inbound, auth=auth)
        parsed = self._command_parser.parse(inbound.text)
        pairing_gate_enabled = (
            self._config.pairing.enabled
            and self._config.pairing.mode != PAIRING_MODE_OFF
        )

        if auth.role == AUTH_ROLE_UNPAIRED and not is_pair_command(inbound.text):
            if not pairing_gate_enabled and parsed is None:
                return inbound
            self._send_local_text(
                envelope,
                "This chat is not paired. Run /pair <code> first.",
            )
            return None

        if parsed is None or is_pair_command(inbound.text):
            return inbound

        allowed, reason = self._authorizer.command_allowed(parsed, auth)
        if allowed:
            return inbound

        self._send_local_text(envelope, f"Permission denied: {reason}")
        return None

    def _send_local_text(
        self,
        envelope: TelegramInboundEnvelope,
        text: str,
        *,
        payload_type: str | None = None,
    ) -> None:
        result = self._delivery.send_text(text=text, target=to_reply_target(envelope))
        if payload_type is None:
            return
        for sent in result.sent_messages:
            self._session_sink.record_outbound(
                session_id=None,
                chat_id=str(envelope.chat_id),
                topic_id=str(envelope.topic_id)
                if envelope.topic_id is not None
                else None,
                payload={"type": payload_type, "text": text},
                telegram_message=sent,
            )

    def _has_active_principal_binding(self, envelope: TelegramInboundEnvelope) -> bool:
        return _runtime_has_active_principal_binding(self, envelope)


def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value) if value else None
