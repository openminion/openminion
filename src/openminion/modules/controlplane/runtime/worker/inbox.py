import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from ...contracts.inbound import canonicalize_inbound_message
from ...contracts.models import AuthContext, InboundMessage
from ...contracts.outbound import to_legacy_payload
from ..audit import emit_audit_event
from ..dispatcher import ControlPlaneDispatcher
from ..security import ScopeAuthorizer, is_pair_command

from openminion.base.time import utc_now_iso as _iso_now


@dataclass
class InboxWorker:
    store: object
    dispatcher: ControlPlaneDispatcher
    authorizer: ScopeAuthorizer | None = None
    rate_limiter: object | None = None
    audit_logger: object | None = None
    lock_owner: str = ""
    max_attempts: int = 8
    max_backoff_s: int = 300

    def __post_init__(self) -> None:
        if not self.lock_owner:
            self.lock_owner = f"inbox-worker:{uuid.uuid4().hex[:12]}"

    def run_once(self) -> dict[str, Any] | None:
        row = self.store.claim_inbox(lock_owner=self.lock_owner)
        if row is None:
            return None

        inbox_id = str(row["inbox_id"])
        try:
            inbound = self._row_to_inbound(row)
            self._audit(
                "channel.message.received",
                channel=str(row["channel"]),
                chat_id=str(row["chat_id"]),
                inbox_id=inbox_id,
            )

            inbound, auth = self._authorize_inbound(inbound)
            result = self._handle_unpaired(row, inbound, auth, inbox_id)
            if result is not None:
                return result

            parsed = self.dispatcher.parser.parse(inbound.text)
            result = self._handle_command_denial(row, inbound, auth, parsed, inbox_id)
            if result is not None:
                return result
            result = self._handle_rate_limit(row, inbound, inbox_id)
            if result is not None:
                return result
            return self._dispatch_to_outbox(row, inbound, inbox_id)
        except Exception as exc:
            return self._mark_retry_or_dead(inbox_id, exc)

    def _authorize_inbound(
        self, inbound: InboundMessage
    ) -> tuple[InboundMessage, AuthContext | None]:
        if self.authorizer is None:
            return inbound, inbound.auth
        auth = self.authorizer.auth_for_inbound(inbound)
        return replace(inbound, auth=auth), auth

    def _handle_unpaired(
        self,
        row: dict[str, Any],
        inbound: InboundMessage,
        auth: AuthContext | None,
        inbox_id: str,
    ) -> dict[str, Any] | None:
        if self.authorizer is None or auth is None or auth.role != "unpaired":
            return None
        if is_pair_command(inbound.text):
            return None
        self._queue_text_reply(
            row=row,
            text="This chat is not paired. Run /pair <code> first.",
            payload_type="auth_error",
        )
        self.store.ack_inbox(inbox_id)
        return {"status": "unpaired", "inbox_id": inbox_id}

    def _handle_command_denial(
        self,
        row: dict[str, Any],
        inbound: InboundMessage,
        auth: AuthContext | None,
        parsed: Any,
        inbox_id: str,
    ) -> dict[str, Any] | None:
        if self.authorizer is None or parsed is None or auth is None:
            return None
        if is_pair_command(inbound.text):
            return None
        allowed, reason = self.authorizer.command_allowed(parsed, auth)
        if allowed:
            return None
        self._queue_text_reply(
            row=row,
            text=f"Permission denied: {reason}",
            payload_type="auth_error",
        )
        self._audit("cp.error", outcome="denied", reason=reason, inbox_id=inbox_id)
        self.store.ack_inbox(inbox_id)
        return {"status": "denied", "inbox_id": inbox_id, "reason": reason}

    def _handle_rate_limit(
        self, row: dict[str, Any], inbound: InboundMessage, inbox_id: str
    ) -> dict[str, Any] | None:
        if _rate_limit_already_checked(inbound):
            return None
        if self.rate_limiter is None or not hasattr(self.rate_limiter, "check"):
            return None
        session_id = self.dispatcher.router.store.resolve_session(
            inbound.user_key, inbound.chat_key
        )
        allowed, reason = self.rate_limiter.check(inbound, session_id)
        if allowed:
            return None
        self._queue_text_reply(
            row=row,
            text=f"Rate limited: {reason}",
            payload_type="rate_limited",
        )
        self._audit(
            "cp.error",
            outcome="rate_limited",
            reason=reason,
            inbox_id=inbox_id,
        )
        self.store.ack_inbox(inbox_id)
        return {"status": "rate_limited", "inbox_id": inbox_id, "reason": reason}

    def _dispatch_to_outbox(
        self, row: dict[str, Any], inbound: InboundMessage, inbox_id: str
    ) -> dict[str, Any]:
        outbound_payload, ctx = self.dispatcher.dispatch(inbound)
        payload = to_legacy_payload(outbound_payload)
        outbox_id = self.store.enqueue_outbox(
            channel=str(row["channel"]),
            chat_id=str(row["chat_id"]),
            thread_id=(str(row["thread_id"]) if row.get("thread_id") else None),
            reply_to=inbound.reply_to or str(row["channel_message_id"]),
            payload=payload,
        )
        self._audit_outbox_enqueued(row, inbound, inbox_id, outbox_id)
        self._touch_bindings(row)
        self._audit_dispatch_done(payload, ctx, inbox_id)
        self.store.ack_inbox(inbox_id)
        return {"status": "done", "inbox_id": inbox_id, "session_id": ctx.session_id}

    def _audit_outbox_enqueued(
        self,
        row: dict[str, Any],
        inbound: InboundMessage,
        inbox_id: str,
        outbox_id: str,
    ) -> None:
        self._audit(
            "cp.outbox.enqueued",
            reason="enqueued",
            outbox_id=outbox_id,
            inbox_id=inbox_id,
            channel=str(row["channel"]),
            chat_id=str(row["chat_id"]),
            update_id=(
                inbound.metadata.get("update_id")
                or inbound.meta.get("update_id")
                or row["channel_message_id"]
                or None
            ),
        )

    def _touch_bindings(self, row: dict[str, Any]) -> None:
        if hasattr(self.store, "touch_pairing"):
            self.store.touch_pairing(
                channel=str(row["channel"]), chat_id=str(row["chat_id"])
            )
        if hasattr(self.store, "touch_channel_subject"):
            self.store.touch_channel_subject(
                channel=str(row["channel"]),
                subject_id=str(row["chat_id"]),
            )

    def _audit_dispatch_done(self, payload: dict[str, Any], ctx: Any, inbox_id: str) -> None:
        self._audit(
            "cp.command.executed"
            if payload.get("type") == "command_result"
            else "cp.chat.dispatched",
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            inbox_id=inbox_id,
        )

    def _mark_retry_or_dead(
        self, inbox_id: str, exc: Exception
    ) -> dict[str, Any]:
        error = str(exc)[:2000]
        status = self.store.mark_inbox_retry(
            inbox_id,
            error=error,
            max_attempts=self.max_attempts,
            max_backoff_s=self.max_backoff_s,
        )
        event_type = "cp.inbox.deadletter" if status == "dead" else "cp.inbox.retry"
        self._audit(event_type, outcome=status, inbox_id=inbox_id, error=error)
        return {"status": status, "inbox_id": inbox_id, "error": error}

    def _row_to_inbound(self, row: dict[str, Any]) -> InboundMessage:
        payload = self._load_payload(row.get("payload_json"))
        user_key = str(
            payload.get("user_key") or payload.get("user_id") or row["user_id"]
        )
        chat_key = str(
            payload.get("chat_key") or payload.get("chat_id") or row["chat_id"]
        )
        channel = str(payload.get("channel") or row["channel"])
        metadata = (
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        )
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        if not metadata and meta:
            metadata = dict(meta)
        if not meta and metadata:
            meta = dict(metadata)
        ts_raw = payload.get("timestamp")
        timestamp = datetime.now(timezone.utc)
        if isinstance(ts_raw, str) and ts_raw.strip():
            try:
                timestamp = datetime.fromisoformat(ts_raw)
            except ValueError:
                timestamp = datetime.now(timezone.utc)
        inbound = InboundMessage(
            user_key=user_key,
            chat_key=chat_key,
            text=str(payload.get("text") or ""),
            channel=channel,
            thread_key=(
                str(payload.get("thread_key")) if payload.get("thread_key") else None
            ),
            attachments=[],
            meta=meta,
            inbound_id=str(row["inbox_id"]),
            channel_message_id=str(row["channel_message_id"]),
            chat_id=str(row["chat_id"]),
            user_id=str(row["user_id"]),
            thread_id=(str(row["thread_id"]) if row.get("thread_id") else None),
            timestamp=timestamp,
            reply_to=(
                str(payload.get("reply_to")) if payload.get("reply_to") else None
            ),
            metadata=metadata,
            auth=self._load_auth(payload.get("auth")),
        )
        return canonicalize_inbound_message(inbound)

    def _load_auth(self, raw: Any) -> AuthContext | None:
        if not isinstance(raw, dict):
            return None
        role = str(raw.get("role") or "").strip()
        if not role:
            return None
        scopes_raw = raw.get("scopes") or ()
        metadata_raw = raw.get("metadata") or {}
        return AuthContext(
            role=role,
            scopes=tuple(str(scope) for scope in scopes_raw if str(scope).strip()),
            principal_id=(
                str(raw.get("principal_id"))
                if raw.get("principal_id") is not None
                else None
            ),
            metadata=dict(metadata_raw) if isinstance(metadata_raw, dict) else {},
        )

    def _queue_text_reply(
        self, *, row: dict[str, Any], text: str, payload_type: str
    ) -> None:
        payload = self._load_payload(row.get("payload_json"))
        self.store.enqueue_outbox(
            channel=str(row["channel"]),
            chat_id=str(row["chat_id"]),
            thread_id=(str(row["thread_id"]) if row.get("thread_id") else None),
            reply_to=str(payload.get("reply_to") or row["channel_message_id"]),
            payload={"type": payload_type, "text": text, "generated_at": _iso_now()},
        )

    def _load_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    def _audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)


def _rate_limit_already_checked(inbound: InboundMessage) -> bool:
    return bool(
        inbound.meta.get("controlplane_rate_limit_checked")
        or inbound.metadata.get("controlplane_rate_limit_checked")
    )
