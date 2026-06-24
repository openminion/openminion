import json
import uuid
from dataclasses import dataclass
from typing import Any

from ...contracts.models import DeliveryContext
from ...interfaces import ensure_controlplane_component_compatibility
from ..audit import emit_audit_event


@dataclass
class OutboxWorker:
    store: object
    registry: object
    audit_logger: object | None = None
    lock_owner: str = ""
    max_attempts: int = 8
    max_backoff_s: int = 300

    def __post_init__(self) -> None:
        if not self.lock_owner:
            self.lock_owner = f"outbox-worker:{uuid.uuid4().hex[:12]}"
        ensure_controlplane_component_compatibility(
            self.registry,
            component_type="channel_registry",
        )

    def run_once(self) -> dict[str, Any] | None:
        row = self.store.claim_outbox(lock_owner=self.lock_owner)
        if row is None:
            return None
        outbox_id = str(row["outbox_id"])
        payload = self._parse_payload(row.get("payload_json"))
        delivery_ctx = DeliveryContext(
            channel=str(row["channel"]),
            chat_id=str(row["chat_id"]),
            thread_id=(str(row["thread_id"]) if row.get("thread_id") else None),
            reply_to=(str(row["reply_to"]) if row.get("reply_to") else None),
            outbox_id=outbox_id,
        )
        try:
            result, route_reason = self._deliver(
                payload=payload, delivery_ctx=delivery_ctx
            )
            self._audit(
                "cp.route.outbox.selected",
                outbox_id=outbox_id,
                channel=delivery_ctx.channel,
                chat_id=delivery_ctx.chat_id,
                reason=route_reason,
            )
            self.store.mark_outbox_sent(outbox_id)
            self._audit(
                "channel.message.sent",
                outbox_id=outbox_id,
                channel=delivery_ctx.channel,
                chat_id=delivery_ctx.chat_id,
                reason=route_reason,
            )
            self._audit(
                "cp.delivery.sent",
                outbox_id=outbox_id,
                channel=delivery_ctx.channel,
                chat_id=delivery_ctx.chat_id,
                reason="delivery_ok",
            )
            return {"status": "sent", "outbox_id": outbox_id, "result": result}
        except Exception as exc:
            self._audit(
                "cp.delivery.failed",
                outbox_id=outbox_id,
                channel=delivery_ctx.channel,
                chat_id=delivery_ctx.chat_id,
                reason="delivery_exception",
                error=str(exc),
            )
            state = self.store.mark_outbox_retry(
                outbox_id,
                error=str(exc),
                max_attempts=self.max_attempts,
                max_backoff_s=self.max_backoff_s,
            )
            if state == "dead":
                self._audit(
                    "cp.outbox.deadletter",
                    outbox_id=outbox_id,
                    error=str(exc),
                    reason="max_attempts_exceeded",
                )
            return {"status": state, "outbox_id": outbox_id, "error": str(exc)}

    def _deliver(
        self,
        *,
        payload: dict[str, Any],
        delivery_ctx: DeliveryContext,
    ) -> tuple[Any, str]:
        adapter = self.registry.get(delivery_ctx.channel)
        return adapter.deliver(payload, delivery_ctx), "registry_route"

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
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
