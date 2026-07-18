import asyncio
import concurrent.futures
import json
import logging
import threading
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable, Coroutine
from uuid import uuid4

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config

from ..contracts.inbound import canonicalize_inbound_message, inbound_metadata
from ..interfaces import (
    CONTROLPLANE_INTERFACE_VERSION,
    ensure_controlplane_component_compatibility,
)
from ..contracts.models import (
    BrainClient,
    CommandParser,
    CommandResult,
    InboundMessage,
    ParsedCommand,
    ResolvedContext,
)
from ..contracts.outbound import (
    OutboundPayload,
    from_legacy_payload,
    payload_type,
    to_legacy_payload,
)
from .audit import emit_audit_event
from .router import Router

if TYPE_CHECKING:
    from ..wizard.runtime import WizardResult
    from ..wizard.store import WizardSession

_LOG = logging.getLogger(__name__)


def _run_coro_in_thread(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run ``coro`` to completion on a dedicated thread with its own event loop."""

    result_box: concurrent.futures.Future[Any] = concurrent.futures.Future()

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            value = loop.run_until_complete(coro)
            result_box.set_result(value)
        except BaseException as exc:  # noqa: BLE001 - propagate to caller
            result_box.set_exception(exc)
        finally:
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)

    thread = threading.Thread(
        target=_runner,
        name="controlplane-dispatcher-coro",
        daemon=True,
    )
    thread.start()
    thread.join()
    return result_box.result()


@dataclass
class ControlPlaneDispatcher:
    contract_version: str = field(default=CONTROLPLANE_INTERFACE_VERSION, init=False)
    store: object
    router: Router
    parser: CommandParser
    command_registry: object
    brain_client: BrainClient
    audit_logger: object | None = None
    outbound_sender: Callable[[dict[str, Any]], None] | None = None
    identity_api: object | None = None
    env: EnvironmentConfig = field(default_factory=resolve_environment_config)
    _pending_clarify_by_session: dict[str, dict] = field(
        default_factory=dict, init=False
    )

    def __post_init__(self) -> None:
        strict_raw = (
            self.env.get("OPENMINION_STRICT_CONTROLPLANE_CONTRACTS", "0")
            .strip()
            .lower()
        )
        strict = strict_raw not in {"", "0", "false", "no", "off"}
        components = (
            ("session_store", self.store),
            ("router", self.router),
            ("command_parser", self.parser),
            ("brain_client", self.brain_client),
        )
        for component_type, component in components:
            try:
                ensure_controlplane_component_compatibility(
                    component, component_type=component_type
                )
            except Exception as exc:
                if strict:
                    raise
                _LOG.warning(
                    "controlplane contract warning (%s): %s",
                    component_type,
                    exc,
                )
        self._hydrate_pending_clarifies_from_store()

    def _hydrate_pending_clarifies_from_store(self) -> None:
        """Seed the in-memory pending-clarify map from the store on init."""
        lister = getattr(self.store, "list_pending_clarifies", None)
        if lister is None:
            return
        try:
            rows = lister()
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            _LOG.warning("controlplane: failed to hydrate pending clarifies: %s", exc)
            return
        if not rows:
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            session_id = str(row.get("session_id", "")).strip()
            if not session_id:
                continue
            self._pending_clarify_by_session[session_id] = dict(row)

    def handle_inbound(self, inbound: InboundMessage) -> dict[str, Any]:
        inbound = canonicalize_inbound_message(inbound)
        self._audit("inbound.received", channel=inbound.channel)
        ctx = self.router.resolve(inbound)
        self._audit(
            "inbound.resolved", session_id=ctx.session_id, agent_id=ctx.agent_id
        )
        self.store.persist_inbound(inbound, ctx.session_id)
        outbound_payload, _ = self.dispatch(inbound)
        payload = to_legacy_payload(outbound_payload)
        if self.outbound_sender is not None:
            self.outbound_sender(payload)
        self._audit("outbound.sent", kind=self._outbound_kind(outbound_payload))
        return payload

    def dispatch(
        self, inbound: InboundMessage
    ) -> tuple[OutboundPayload, ResolvedContext]:
        inbound = canonicalize_inbound_message(inbound)
        wizard_result = self._try_dispatch_to_wizard(inbound)
        if wizard_result is not None:
            payload, ctx = wizard_result
            return self._to_outbound(payload, ctx), ctx

        ctx = self.router.resolve(inbound)
        pending = self._pending_clarify_by_session.get(ctx.session_id)
        inbound = self._apply_pending_trace(inbound, pending)
        inbound_trace = str(inbound_metadata(inbound).get("trace_id", "")).strip()
        if inbound_trace and inbound_trace != ctx.trace_id:
            ctx = replace(ctx, trace_id=inbound_trace)

        clarify_answer = self._extract_clarify_answer(inbound)
        if clarify_answer is not None:
            unknown_payload = self._maybe_unknown_clarify_payload(
                ctx=ctx,
                clarify_answer=clarify_answer,
                pending=self._pending_clarify_by_session.get(ctx.session_id),
            )
            if unknown_payload is not None:
                return self._to_outbound(unknown_payload, ctx), ctx
            self._audit(
                "cp.clarify.answered",
                session_id=ctx.session_id,
                trace_id=ctx.trace_id,
                clarify_id=clarify_answer.get("clarify_id", ""),
                question_id=clarify_answer.get("question_id", ""),
            )
            self._audit(
                "cp.resume.dispatched",
                session_id=ctx.session_id,
                trace_id=ctx.trace_id,
                clarify_id=clarify_answer.get("clarify_id", ""),
            )

        command = self.parser.parse(inbound.text)
        if command is not None:
            payload = self._dispatch_command(command, ctx)
        else:
            payload = self._dispatch_chat(inbound, ctx)

        return self._to_outbound(payload, ctx), ctx

    def _try_dispatch_to_wizard(
        self, inbound: InboundMessage
    ) -> tuple[dict, ResolvedContext] | None:
        """Try to dispatch to an active wizard session."""

        wizard_id = ""

        async def async_lookup():
            nonlocal wizard_id
            from ..wizard.runtime import get_wizard_executor
            from ..wizard.store import get_wizard_store

            wizard_store = await get_wizard_store()
            active_sessions = []
            if inbound.chat_key:
                active_sessions.extend(
                    await wizard_store.get_active_sessions_for_chat(inbound.chat_key)
                )
            if not active_sessions and inbound.user_key:
                active_sessions.extend(
                    await wizard_store.get_active_sessions_for_user(inbound.user_key)
                )

            if not active_sessions:
                return None

            most_recent = max(active_sessions, key=lambda s: s.updated_at)
            wizard_id = str(most_recent.wizard_id)
            wizard_executor = await get_wizard_executor()
            result = await wizard_executor.process_input(
                most_recent.wizard_id, inbound.text
            )
            resolved_ctx = self.router.resolve(inbound)
            ctx = replace(resolved_ctx, wizard_session_id=most_recent.wizard_id)
            payload = self._convert_wizard_result_to_payload(result, ctx, most_recent)

            return payload, ctx

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        try:
            if running_loop is None:
                return asyncio.run(async_lookup())
            return _run_coro_in_thread(async_lookup())
        except Exception as exc:
            session_id = ""
            try:
                session_id = self.router.resolve(inbound).session_id
            except Exception:  # noqa: BLE001 - audit best-effort only
                pass
            _LOG.warning(
                "cp.wizard.step.failure",
                extra={
                    "exc_type": type(exc).__name__,
                    "session_id": session_id,
                    "wizard_id": wizard_id,
                },
            )
            self._audit(
                "cp.wizard.step.failed",
                exc_type=type(exc).__name__,
                message=str(exc),
                session_id=session_id,
                wizard_id=wizard_id,
            )
            raise

    def _convert_wizard_result_to_payload(
        self,
        wizard_result: "WizardResult",
        ctx: ResolvedContext,
        session: "WizardSession" = None,
    ) -> dict:
        if wizard_result.error:
            return {
                "type": "wizard_result",
                "ok": wizard_result.success if wizard_result.success else False,
                "text": f"Error: {wizard_result.error}",
                "data": wizard_result.data,
                "session_id": ctx.session_id,
                "agent_id": ctx.agent_id,
                "status": "error",
                "completed": wizard_result.completed,
                "canceled": wizard_result.canceled,
            }
        if wizard_result.canceled:
            return {
                "type": "wizard_result",
                "ok": True,  # Consider cancel as a successful operation
                "text": "Operation was cancelled.",
                "data": wizard_result.data,
                "session_id": ctx.session_id,
                "agent_id": ctx.agent_id,
                "status": "cancelled",
                "canceled": True,
                "completed": False,
            }
        action = (
            wizard_result.data.get("action", "response")
            if isinstance(wizard_result.data, dict)
            else "response"
        )
        text_output = self._generate_wizard_output(wizard_result, action)

        return {
            "type": "wizard_result",
            "ok": wizard_result.success,
            "text": text_output,
            "data": wizard_result.data,
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
            "status": "completed" if wizard_result.completed else "active",
            "completed": wizard_result.completed,
            "canceled": wizard_result.canceled,
            "action": action,
            "session_detail": {}
            if not session
            else {
                "step": session.step,
                "total_steps": session.total_steps,
                "command_name": session.command_name,
            },
        }

    def _generate_wizard_output(
        self, wizard_result: "WizardResult", action: str
    ) -> str:
        """Generate human-readable output for wizard results."""
        if not wizard_result.data:
            if getattr(wizard_result, "completed", False):
                return "Wizard completed successfully."
            return "Wizard step processed."

        data = wizard_result.data

        if action == "show_help":
            return data.get("help_text", "Help information is available.")
        if action == "preview":
            changes_msg = ", ".join(
                [f"{k}={v}" for k, v in data.get("changes", {}).items()]
            )
            return f"Preview of changes: {changes_msg if changes_msg else 'No changes yet.'}"
        if action == "next_step":
            return data.get("next_prompt", "Wizard prompt:")
        if action == "incomplete":
            return "More information needed. Please provide additional details."
        if action == "cancelled":
            return "Wizard process cancelled as requested."
        if getattr(wizard_result, "completed", False):
            return "Wizard completed successfully."
        return "Wizard step processed."

    def _dispatch_command(self, command: ParsedCommand, ctx: ResolvedContext) -> dict:
        self._audit(
            "cp.command.detected",
            canonical=command.canonical,
            session_id=ctx.session_id,
        )
        result: CommandResult = self.command_registry.execute(command, ctx)
        event = "cp.command.executed" if result.ok else "cp.command.failed"
        self._audit(event, canonical=command.canonical, session_id=ctx.session_id)
        return {
            "type": "command_result",
            "ok": result.ok,
            "text": result.text,
            "data": result.data,
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
        }

    def _dispatch_chat(self, inbound: InboundMessage, ctx: ResolvedContext) -> dict:
        attachment_refs = []
        if hasattr(self.store, "attachment_refs_from_inputs"):
            attachment_refs = self.store.attachment_refs_from_inputs(
                inbound.attachments
            )
        turn_meta: dict[str, object] = {}
        clarify_answer = self._extract_clarify_answer(inbound)
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
        clarify_payload = self._extract_clarify_request(
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
            self._pending_clarify_by_session[ctx.session_id] = pending_entry
            self._store_set_pending_clarify(ctx.session_id, pending_entry)
            self._audit(
                "cp.clarify.requested",
                session_id=ctx.session_id,
                trace_id=ctx.trace_id,
                clarify_id=clarify_payload.get("clarify_id", ""),
                blocking=bool(clarify_payload.get("blocking", True)),
                question_count=len(clarify_payload.get("questions", [])),
            )
        elif ctx.session_id in self._pending_clarify_by_session:
            self._pending_clarify_by_session.pop(ctx.session_id, None)
            self._store_clear_pending_clarify(ctx.session_id)
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

    def _apply_pending_trace(
        self, inbound: InboundMessage, pending: dict | None
    ) -> InboundMessage:
        metadata = inbound_metadata(inbound)
        if str(metadata.get("trace_id", "")).strip():
            return inbound
        if not pending:
            return inbound
        if self._extract_clarify_answer(inbound) is None:
            return inbound
        trace_id = str(pending.get("trace_id", "")).strip()
        if not trace_id:
            return inbound
        metadata["trace_id"] = trace_id
        return replace(inbound, metadata=metadata, meta=dict(metadata))

    def _extract_clarify_answer(self, inbound: InboundMessage) -> dict[str, str] | None:
        meta = inbound_metadata(inbound)
        raw = meta.get("clarify_answer")
        if not isinstance(raw, dict):
            return None
        answer = str(raw.get("answer", "")).strip()
        question_id = str(raw.get("question_id", "")).strip()
        clarify_id = str(raw.get("clarify_id", "")).strip()
        if not answer:
            return None
        return {
            "answer": answer,
            "question_id": question_id,
            "clarify_id": clarify_id,
        }

    def _maybe_unknown_clarify_payload(
        self,
        *,
        ctx: ResolvedContext,
        clarify_answer: dict[str, str],
        pending: dict | None,
    ) -> dict | None:
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

    def _extract_brain_status(self, brain_output: dict) -> str:
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

    def _extract_clarify_request(
        self,
        *,
        brain_output: dict,
        session_id: str,
        trace_id: str,
        fallback_text: str,
    ) -> dict | None:
        request = brain_output.get("clarify_request")
        if isinstance(request, dict):
            return self._normalize_clarify_request(
                request=request,
                session_id=session_id,
                trace_id=trace_id,
                fallback_text=fallback_text,
            )
        metadata = brain_output.get("metadata")
        if isinstance(metadata, dict):
            raw = metadata.get("clarify_request")
            if isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict):
                    return self._normalize_clarify_request(
                        request=parsed,
                        session_id=session_id,
                        trace_id=trace_id,
                        fallback_text=fallback_text,
                    )
        return None

    def _normalize_clarify_request(
        self,
        *,
        request: dict,
        session_id: str,
        trace_id: str,
        fallback_text: str,
    ) -> dict:
        raw_questions = request.get("questions")
        questions: list[dict] = []
        if isinstance(raw_questions, list):
            for item in raw_questions:
                if not isinstance(item, dict):
                    continue
                q_id = str(item.get("id", "")).strip() or uuid4().hex
                q_text = str(item.get("question", "")).strip()
                if not q_text:
                    continue
                questions.append(
                    {
                        "id": q_id,
                        "type": str(
                            item.get("type", "ambiguous_input") or "ambiguous_input"
                        ),
                        "question": q_text,
                        "options": item.get("options")
                        if isinstance(item.get("options"), list)
                        else None,
                        "default_value": item.get("default_value"),
                        "is_blocking": bool(item.get("is_blocking", True)),
                    }
                )
        if not questions and fallback_text.strip():
            questions.append(
                {
                    "id": uuid4().hex,
                    "type": "ambiguous_input",
                    "question": fallback_text.strip(),
                    "options": None,
                    "default_value": None,
                    "is_blocking": True,
                }
            )
        clarify_id = str(request.get("clarify_id", "")).strip() or uuid4().hex
        return {
            "clarify_id": clarify_id,
            "trace_id": str(request.get("trace_id", "")).strip() or trace_id,
            "session_id": str(request.get("session_id", "")).strip() or session_id,
            "questions": questions,
            "blocking": bool(request.get("blocking", True)),
            "defaults_used": request.get("defaults_used", {}),
        }

    def _store_set_pending_clarify(
        self, session_id: str, payload: dict[str, Any]
    ) -> None:
        setter = getattr(self.store, "set_pending_clarify", None)
        if setter is None:
            return
        try:
            setter(session_id, dict(payload))
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            _LOG.warning(
                "controlplane: failed to persist pending clarify (%s): %s",
                session_id,
                exc,
            )

    def _store_clear_pending_clarify(self, session_id: str) -> None:
        clearer = getattr(self.store, "clear_pending_clarify", None)
        if clearer is None:
            return
        try:
            clearer(session_id)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            _LOG.warning(
                "controlplane: failed to clear pending clarify (%s): %s",
                session_id,
                exc,
            )

    def _audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)

    def _to_outbound(self, payload: dict, ctx: ResolvedContext) -> OutboundPayload:
        return from_legacy_payload(payload, ctx=ctx)

    @staticmethod
    def _outbound_kind(outbound_payload: OutboundPayload) -> str:
        outbound_type = payload_type(outbound_payload)
        if outbound_type == "command_result":
            return "command"
        if outbound_type == "clarify_error":
            return "clarify_error"
        return "chat"
