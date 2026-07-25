from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ...contracts.inbound import inbound_metadata
from ...contracts.models import BrainClient, InboundMessage, ResolvedContext
from ..audit import emit_audit_event
from .clarify import (
    ClarifyStateManager,
    extract_clarify_answer,
    extract_clarify_request,
)

JsonDict = dict[str, Any]


@dataclass
class ChatDispatcher:
    store: object
    brain_client: BrainClient
    audit_logger: object | None
    clarify: ClarifyStateManager

    def dispatch(self, inbound: InboundMessage, ctx: ResolvedContext) -> JsonDict:
        attachment_refs = []
        if hasattr(self.store, "attachment_refs_from_inputs"):
            attachment_refs = self.store.attachment_refs_from_inputs(
                inbound.attachments
            )
        turn_meta: dict[str, object] = {}
        clarify_answer = extract_clarify_answer(inbound)
        if clarify_answer is not None:
            turn_meta["clarify_answer"] = clarify_answer
        if hasattr(self.store, "append_turn"):
            self.store.append_turn(
                session_id=ctx.session_id,
                role="user",
                content=inbound.text,
                attachments=attachment_refs,
                meta=turn_meta,
            )
        brain_output = self.brain_client.run(
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            user_text=inbound.text,
            attachment_refs=attachment_refs,
            trace_id=ctx.trace_id,
        )
        text = str(brain_output.get("text", "") or "")
        status = self._extract_brain_status(brain_output)
        clarify_payload = extract_clarify_request(
            brain_output=brain_output,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            fallback_text=text,
        )
        if status == "waiting_user" and clarify_payload is not None:
            pending_entry = {
                "clarify_id": clarify_payload.get("clarify_id", ""),
                "trace_id": str(brain_output.get("trace_id", "") or ctx.trace_id),
                "session_id": ctx.session_id,
                "questions": clarify_payload.get("questions", []),
            }
            self.clarify.set(ctx.session_id, pending_entry)
            self._audit(
                "cp.clarify.requested",
                session_id=ctx.session_id,
                trace_id=ctx.trace_id,
                clarify_id=clarify_payload.get("clarify_id", ""),
                blocking=bool(clarify_payload.get("blocking", True)),
                question_count=len(clarify_payload.get("questions", [])),
            )
        elif self.clarify.get(ctx.session_id) is not None:
            self.clarify.clear(ctx.session_id)
        self._audit(
            "cp.chat.dispatched", session_id=ctx.session_id, agent_id=ctx.agent_id
        )
        return {
            "type": "chat",
            "text": text,
            "status": status,
            "clarify": clarify_payload,
            "data": dict(brain_output),
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
        }

    def apply_pending_trace(
        self, inbound: InboundMessage, pending: JsonDict | None
    ) -> InboundMessage:
        metadata = inbound_metadata(inbound)
        if str(metadata.get("trace_id", "")).strip():
            return inbound
        if not pending:
            return inbound
        if extract_clarify_answer(inbound) is None:
            return inbound
        trace_id = str(pending.get("trace_id", "")).strip()
        if not trace_id:
            return inbound
        metadata["trace_id"] = trace_id
        return replace(inbound, metadata=metadata, meta=dict(metadata))

    def maybe_unknown_clarify_payload(
        self,
        *,
        ctx: ResolvedContext,
        clarify_answer: dict[str, str],
        pending: JsonDict | None,
    ) -> JsonDict | None:
        provided_id = str(clarify_answer.get("clarify_id", "")).strip()
        if pending is None:
            if not provided_id:
                return None
            self._audit(
                "cp.clarify.answer_rejected",
                session_id=ctx.session_id,
                trace_id=ctx.trace_id,
                clarify_id=provided_id,
                reason="unknown_clarify_id",
            )
            return {
                "type": "clarify_error",
                "ok": False,
                "status": "waiting_user",
                "text": f"Unknown clarify_id '{provided_id}'.",
                "session_id": ctx.session_id,
                "agent_id": ctx.agent_id,
                "data": {
                    "error_code": "UNKNOWN_CLARIFY_ID",
                    "clarify_id": provided_id,
                },
            }
        expected_id = str(pending.get("clarify_id", "")).strip()
        if provided_id and expected_id and provided_id != expected_id:
            self._audit(
                "cp.clarify.answer_rejected",
                session_id=ctx.session_id,
                trace_id=ctx.trace_id,
                clarify_id=provided_id,
                expected_clarify_id=expected_id,
                reason="unknown_clarify_id",
            )
            return {
                "type": "clarify_error",
                "ok": False,
                "status": "waiting_user",
                "text": f"Unknown clarify_id '{provided_id}'.",
                "session_id": ctx.session_id,
                "agent_id": ctx.agent_id,
                "clarify": {
                    "clarify_id": expected_id,
                    "trace_id": pending.get("trace_id", ""),
                    "session_id": pending.get("session_id", ctx.session_id),
                    "questions": pending.get("questions", []),
                    "blocking": True,
                },
                "data": {
                    "error_code": "UNKNOWN_CLARIFY_ID",
                    "clarify_id": provided_id,
                    "expected_clarify_id": expected_id,
                },
            }
        return None

    def _extract_brain_status(self, brain_output: JsonDict) -> str:
        direct = str(brain_output.get("status", "")).strip().lower()
        if direct:
            return direct
        metadata = brain_output.get("metadata")
        if isinstance(metadata, dict):
            for key in ("brain_status", "status"):
                value = str(metadata.get(key, "")).strip().lower()
                if value:
                    return value
        return "completed"

    def _audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)
