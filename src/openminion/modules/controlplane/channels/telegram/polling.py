from dataclasses import replace
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

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
from openminion.modules.controlplane.channels.telegram.reactions import (
    maybe_register_reactions_adapter,
)
from openminion.modules.controlplane.channels.telegram.events import (
    NoopSessionEventSink,
)
from openminion.modules.controlplane.channels.telegram.state import (
    TelegramPollStateStore,
)
from openminion.modules.controlplane.channels.telegram.runtime.helpers import (
    _audit_event as _runtime_audit_event,
    _chat_action_pulse as _runtime_chat_action_pulse,
    _deliver_sync_fallback as _runtime_deliver_sync_fallback,
    _dispatch_runtime_with_parity_error,
    _enqueue_outbox as _runtime_enqueue_outbox,
    _has_active_principal_binding as _runtime_has_active_principal_binding,
    _resolve_controlplane_auth_store,
    _resolve_controlplane_clarify_session_id,
    _resolve_controlplane_clarify_store,
    _resolve_controlplane_pairing_store,
    _resolve_reply_target,
    _send_runner_online_notice as _runtime_send_runner_online_notice,
    _validate_component_contracts as _runtime_validate_component_contracts,
)
from openminion.base.config.env import resolve_environment_config

_LOG = logging.getLogger(__name__)


class RuntimeHandler(Protocol):
    contract_version: str

    def handle_inbound(self, inbound: Any) -> dict[str, Any]: ...


class RecentUpdateIds:
    def __init__(self, max_size: int = 1000) -> None:
        self._max_size = max(100, max_size)
        self._items: OrderedDict[int, None] = OrderedDict()

    def contains(self, update_id: int) -> bool:
        return update_id in self._items

    def add(self, update_id: int) -> None:
        if update_id in self._items:
            self._items.move_to_end(update_id)
            return
        self._items[update_id] = None
        if len(self._items) > self._max_size:
            self._items.popitem(last=False)


class TelegramPollingRunner:
    contract_version = TELEGRAM_INTERFACE_VERSION
    channel_id = "telegram"

    def __init__(
        self,
        *,
        config: TelegramChannelConfig,
        api: TelegramBotAPI,
        runtime: RuntimeHandler,
        delivery: TelegramDeliveryService,
        state_store: TelegramPollStateStore | None = None,
        session_sink: SessionEventSinkAPI | None = None,
        pairing_service: TelegramPairingService | None = None,
        access_policy: AccessPolicyAPI | None = None,
        audit_logger: object | None = None,
        account_id: str | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
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
        self._sleep = sleep_fn
        self._log = logger or _LOG
        self._env = (
            dict(env) if env is not None else resolve_environment_config().snapshot()
        )
        self._pairing = pairing_service
        self._audit_logger = audit_logger
        self._store = store if store is not None else getattr(runtime, "store", None)
        self._outbox_worker = outbox_worker
        self._outbox_thread: threading.Thread | None = None
        self._outbox_managed_by_supervisor = False
        self._rate_limiter = rate_limiter
        self._brain_client = brain_client
        if self._pairing is None and self._state_store is not None:
            self._pairing = TelegramPairingService(
                config=self._config.pairing,
                store=self._state_store,
                controlplane_store=_resolve_controlplane_pairing_store(self._runtime),
                audit_logger=self._audit_logger,
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
        self._last_update_id = 0
        self._initialized = False
        self._recent_ids = RecentUpdateIds(max_size=1000)
        self._last_poll_started_ts: float | None = None
        self._last_poll_success_ts: float | None = None
        self._last_poll_error: str | None = None
        self._polling_lease_acquired = False
        self._polling_lease_command = "openminion channel telegram run"
        self._runner_online_notice_sent = False
        self._validate_component_contracts()

    def _validate_component_contracts(self) -> None:
        _runtime_validate_component_contracts(self)

    def initialize(self) -> None:
        if self._initialized:
            return

        me = self._api.get_me()
        self._bot_username = _as_str_or_none(me.get("username"))
        bot_id = _as_str_or_none(me.get("id"))
        if bot_id:
            self._account_id = f"telegram-bot:{bot_id}"

        if self._config.actions.reactions:
            maybe_register_reactions_adapter(self._api)

        self._acquire_polling_lease()

        try:
            self._api.delete_webhook(
                drop_pending_updates=self._config.polling.drop_pending_on_start,
            )

            if self._config.polling.persist_offset and self._state_store is not None:
                self._last_update_id = self._state_store.get_last_update_id(
                    self._account_id
                )

            if self._config.polling.drop_pending_on_start:
                self._drop_pending_updates()

            self._initialized = True
        except Exception:
            self._release_polling_lease()
            raise

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        self.initialize()
        consecutive_errors = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                self.run_once()
                consecutive_errors = 0
            except Exception as exc:
                consecutive_errors += 1
                self._last_poll_error = str(exc)
                self._log.exception("telegram polling loop error: %s", exc)
                self._sleep(self._resolve_backoff_seconds(consecutive_errors))

    def start(self, stop_event: threading.Event | None = None) -> None:
        from openminion.modules.controlplane.runtime.gate import (
            TELEGRAM_INGRESS_REQUIRED_MODULES,
            assert_controlplane_lane,
        )

        assert_controlplane_lane(
            ingress="telegram_polling",
            required_modules=TELEGRAM_INGRESS_REQUIRED_MODULES,
        )

        self._start_outbox_worker(stop_event)
        try:
            self.initialize()
            self._send_runner_online_notice()
            self.run_forever(stop_event)
        finally:
            self._stop_outbox_worker()
            self._release_polling_lease()
            self._close_state_store()

    def stop(self) -> None:
        self._stop_outbox_worker()
        self._close_brain_client()
        self._release_polling_lease()
        self._close_state_store()

    def _start_outbox_worker(self, stop_event: threading.Event | None) -> None:
        if self._outbox_managed_by_supervisor:
            return
        if self._outbox_worker is None or stop_event is None:
            return
        if self._outbox_thread is not None and self._outbox_thread.is_alive():
            return
        thread = threading.Thread(
            target=self._run_outbox_loop,
            args=(stop_event,),
            daemon=True,
            name=f"outbox-worker-{self.channel_id}",
        )
        self._outbox_thread = thread
        thread.start()

    def _stop_outbox_worker(self) -> None:
        if self._outbox_managed_by_supervisor:
            return
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

    def _run_outbox_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                result = self._outbox_worker.run_once()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                self._log.exception("outbox worker run_once failed: %s", exc)
                self._sleep(0.1)
                continue
            if result is None:
                self._sleep(0.1)

    def _send_runner_online_notice(self) -> None:
        _runtime_send_runner_online_notice(self)

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

    def run_once(self) -> int:
        self.initialize()
        self._heartbeat_polling_lease()
        offset = self._last_update_id + 1
        self._last_poll_started_ts = time.time()
        updates = self._api.get_updates(
            offset=offset,
            timeout=self._config.polling.timeout_seconds,
            limit=self._config.polling.limit,
            allowed_updates=self._config.allowed_updates,
        )
        self._last_poll_success_ts = time.time()
        self._last_poll_error = None
        if not updates:
            return 0

        processed_count = 0
        committed_update_id = self._last_update_id

        for update in updates:
            update_id = _extract_update_id(update)
            if update_id is None:
                self._log.warning("skipping update without update_id")
                continue

            if self._recent_ids.contains(update_id):
                committed_update_id = max(committed_update_id, update_id)
                continue

            try:
                self._process_update(update)
            except Exception as exc:
                self._log.exception(
                    "failed to process update_id=%s: %s", update_id, exc
                )
                break

            processed_count += 1
            committed_update_id = max(committed_update_id, update_id)
            self._recent_ids.add(update_id)

        if committed_update_id > self._last_update_id:
            self._commit_offset(committed_update_id)

        return processed_count

    def _process_update(self, update: dict[str, Any]) -> dict[str, Any] | None:
        envelope = extract_envelope(update)
        if envelope is None:
            raise ValueError("unsupported or malformed telegram update")

        control_event = to_control_event(envelope)
        self._session_sink.record_inbound(control_event, envelope.raw_update)

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

        # enforce rate limits after auth/scope gating but BEFORE
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
        with _runtime_chat_action_pulse(self, envelope=envelope):
            payload, dispatch_error = _dispatch_runtime_with_parity_error(
                runtime=self._runtime,
                inbound=inbound,
                envelope=envelope,
                audit_event=self._audit_event,
                logger=self._log,
            )
        if dispatch_error is not None:
            self._last_poll_error = str(dispatch_error.get("error") or "")
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
                rendered_text = render_clarify_prompt(
                    clarify_request,
                    max_questions=self._config.clarify.max_questions_per_message,
                    answer_prefix=self._config.clarify.answer_prefix,
                )
                payload = dict(payload)
                payload["text"] = rendered_text
                payload["clarify"] = clarify_request
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

    def _answer_callback_if_needed(self, envelope: TelegramInboundEnvelope) -> None:
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
        """CPD-03: returns True (and short-circuits dispatch) if the
        rate limiter rejects this message. Resolves the canonical session
        id via the dispatcher's router so the per-session window keys
        match the resolution used by ``handle_inbound``.
        """
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
        """CPD-03: user-facing throttle reply via the same delivery path
        used for other local responses. Kept intentionally short — the
        machine-readable detail lives in the audit event.
        """
        if not self._config.actions.send_message:
            return
        self._send_local_text(
            envelope,
            "Rate limit exceeded — please slow down.",
            payload_type="rate_limit",
        )

    def _close_brain_client(self) -> None:
        """CPD-06: best-effort close of an attached brain client during
        shutdown. EchoBrain has no ``close``; OpenMinionBrainClient does
        and is the production target.
        """
        if self._outbox_managed_by_supervisor:
            return
        brain = self._brain_client
        if brain is None or not hasattr(brain, "close"):
            return
        try:
            brain.close()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("brain_client.close failed: %s", exc, exc_info=True)

    def _acquire_polling_lease(self) -> None:
        if self._state_store is None or self._polling_lease_acquired:
            return
        acquire = getattr(self._state_store, "acquire_polling_lease", None)
        if not callable(acquire):
            return
        lease = acquire(
            account_id=self._account_id,
            command=self._polling_lease_command,
        )
        if not lease.acquired:
            raise RuntimeError(lease.diagnostic())
        self._polling_lease_acquired = True

    def _heartbeat_polling_lease(self) -> None:
        if self._state_store is None or not self._polling_lease_acquired:
            return
        heartbeat = getattr(self._state_store, "heartbeat_polling_lease", None)
        if callable(heartbeat):
            heartbeat(account_id=self._account_id)

    def _release_polling_lease(self) -> None:
        if self._state_store is None or not self._polling_lease_acquired:
            return
        release = getattr(self._state_store, "release_polling_lease", None)
        if callable(release):
            release(account_id=self._account_id)
        self._polling_lease_acquired = False

    def _close_state_store(self) -> None:
        close = getattr(self._state_store, "close", None)
        if callable(close):
            close()

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

    def _drop_pending_updates(self) -> None:
        updates = self._api.get_updates(
            offset=-1,
            timeout=0,
            limit=1,
            allowed_updates=self._config.allowed_updates,
        )
        if not updates:
            return

        newest = 0
        for item in updates:
            uid = _extract_update_id(item)
            if uid is not None:
                newest = max(newest, uid)

        if newest > 0:
            self._commit_offset(newest)

    def _commit_offset(self, last_update_id: int) -> None:
        self._last_update_id = int(last_update_id)
        if self._config.polling.persist_offset and self._state_store is not None:
            self._state_store.set_last_update_id(self._account_id, self._last_update_id)

    def _resolve_backoff_seconds(self, consecutive_errors: int) -> float:
        configured = [
            float(value)
            for value in self._config.polling.backoff_seconds
            if int(value) >= 0
        ]
        if not configured:
            return 1.0
        idx = min(max(1, consecutive_errors) - 1, len(configured) - 1)
        return max(0.0, configured[idx])

    def _handle_local_command(
        self, envelope: TelegramInboundEnvelope, text: str
    ) -> bool:
        stripped = (text or "").strip()
        if not stripped.startswith("/"):
            return False

        command = stripped.split(maxsplit=1)[0].lower()
        if command == "/diag":
            self._send_local_text(
                envelope, self._build_diag_text(), payload_type="diag"
            )
            return True

        if command == "/pair":
            if (
                self._pairing is None
                or not self._config.pairing.enabled
                or self._config.pairing.mode == PAIRING_MODE_OFF
            ):
                self._send_local_text(
                    envelope, "Pairing is disabled.", payload_type="pair"
                )
                return True
            if self._has_active_principal_binding(envelope):
                session_scope = str(envelope.chat_id)
                if envelope.topic_id is not None:
                    session_scope = f"{session_scope}:{envelope.topic_id}"
                self._send_local_text(
                    envelope,
                    (
                        "Paired ✅\n"
                        f"chat_id={envelope.chat_id}\n"
                        f"user_id={envelope.from_user.id}\n"
                        f"session_scope={session_scope}"
                    ),
                    payload_type="pair",
                )
                return True
            self._send_local_text(
                envelope,
                "Pairing required. Ask the owner for a fresh /start token.",
                payload_type="pair",
            )
            return True

        return False

    def _build_diag_text(self) -> str:
        return (
            "telegram adapter diag\n"
            "mode=polling\n"
            f"last_update_id={self._last_update_id}\n"
            f"last_poll_started={_format_ts(self._last_poll_started_ts)}\n"
            f"last_poll_success={_format_ts(self._last_poll_success_ts)}\n"
            f"last_error={self._last_poll_error or 'none'}"
        )

    def _maybe_send_pairing_hint(
        self, envelope: TelegramInboundEnvelope, *, reason: str
    ) -> None:
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
            payload_type="pairing_hint",
        )

    def _send_local_text(
        self, envelope: TelegramInboundEnvelope, text: str, *, payload_type: str
    ) -> None:
        if not self._config.actions.send_message:
            return
        target = to_reply_target(envelope)
        result = self._delivery.send_text(text=text, target=target)
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
                payload_type="auth_error",
            )
            return None

        if parsed is None or is_pair_command(inbound.text):
            return inbound

        allowed, reason = self._authorizer.command_allowed(parsed, auth)
        if allowed:
            return inbound

        self._send_local_text(
            envelope,
            f"Permission denied: {reason}",
            payload_type="auth_error",
        )
        return None

    def _has_active_principal_binding(self, envelope: TelegramInboundEnvelope) -> bool:
        return _runtime_has_active_principal_binding(self, envelope)


def _extract_update_id(update: dict[str, Any]) -> int | None:
    try:
        return int(update.get("update_id"))
    except (TypeError, ValueError):
        return None


def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _format_ts(value: float | None) -> str:
    if value is None:
        return "never"
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
