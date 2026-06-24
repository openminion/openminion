import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from ...contracts.inbound import canonicalize_inbound_message
from ...contracts.models import InboundMessage
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

            if self.authorizer is not None:
                auth = self.authorizer.auth_for_inbound(inbound)
                inbound = replace(inbound, auth=auth)
            else:
                auth = inbound.auth

            if (
                self.authorizer is not None
                and auth is not None
                and auth.role == "unpaired"
                and not is_pair_command(inbound.text)
            ):
                self._queue_text_reply(
                    row=row,
                    text="This chat is not paired. Run /pair <code> first.",
                    payload_type="auth_error",
                )
                self.store.ack_inbox(inbox_id)
                return {"status": "unpaired", "inbox_id": inbox_id}

            parsed = self.dispatcher.parser.parse(inbound.text)
            if (
                self.authorizer is not None
                and parsed is not None
                and auth is not None
                and not is_pair_command(inbound.text)
            ):
                allowed, reason = self.authorizer.command_allowed(parsed, auth)
                if not allowed:
                    self._queue_text_reply(
                        row=row,
                        text=f"Permission denied: {reason}",
                        payload_type="auth_error",
                    )
                    self._audit(
                        "cp.error", outcome="denied", reason=reason, inbox_id=inbox_id
                    )
                    self.store.ack_inbox(inbox_id)
                    return {"status": "denied", "inbox_id": inbox_id, "reason": reason}

            # Pre-check rate limits on resolved session id.
            session_id = self.dispatcher.router.store.resolve_session(
                inbound.user_key, inbound.chat_key
            )
            if self.rate_limiter is not None and hasattr(self.rate_limiter, "check"):
                allowed, reason = self.rate_limiter.check(inbound, session_id)
                if not allowed:
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
                    return {
                        "status": "rate_limited",
                        "inbox_id": inbox_id,
                        "reason": reason,
                    }

            outbound_payload, ctx = self.dispatcher.dispatch(inbound)
            payload = to_legacy_payload(outbound_payload)
            self.store.enqueue_outbox(
                channel=str(row["channel"]),
                chat_id=str(row["chat_id"]),
                thread_id=(str(row["thread_id"]) if row.get("thread_id") else None),
                reply_to=str(row["channel_message_id"]),
                payload=payload,
            )
            if hasattr(self.store, "touch_pairing"):
                self.store.touch_pairing(
                    channel=str(row["channel"]), chat_id=str(row["chat_id"])
                )
            if hasattr(self.store, "touch_channel_subject"):
                self.store.touch_channel_subject(
                    channel=str(row["channel"]),
                    subject_id=str(row["chat_id"]),
                )
            self._audit(
                "cp.command.executed"
                if payload.get("type") == "command_result"
                else "cp.chat.dispatched",
                session_id=ctx.session_id,
                agent_id=ctx.agent_id,
                inbox_id=inbox_id,
            )
            self.store.ack_inbox(inbox_id)
            return {
                "status": "done",
                "inbox_id": inbox_id,
                "session_id": ctx.session_id,
            }
        except Exception as exc:
            self.store.fail_inbox(inbox_id, str(exc))
            self._audit("cp.error", outcome="failed", inbox_id=inbox_id, error=str(exc))
            return {"status": "failed", "inbox_id": inbox_id, "error": str(exc)}

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
        )
        return canonicalize_inbound_message(inbound)

    def _queue_text_reply(
        self, *, row: dict[str, Any], text: str, payload_type: str
    ) -> None:
        self.store.enqueue_outbox(
            channel=str(row["channel"]),
            chat_id=str(row["chat_id"]),
            thread_id=(str(row["thread_id"]) if row.get("thread_id") else None),
            reply_to=str(row["channel_message_id"]),
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
