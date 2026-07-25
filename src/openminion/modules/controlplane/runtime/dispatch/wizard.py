from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Coroutine, TypeVar

from ...contracts.inbound import canonicalize_inbound_message
from ...contracts.models import InboundMessage, ResolvedContext
from ..audit import emit_audit_event
from ..router import Router

if TYPE_CHECKING:
    from ...wizard.runtime import WizardResult
    from ...wizard.store import WizardSession

_LOG = logging.getLogger(__name__)
JsonDict = dict[str, Any]
_T = TypeVar("_T")


def _run_coro_in_thread(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` to completion on a dedicated thread with its own event loop."""

    result_box: concurrent.futures.Future[_T] = concurrent.futures.Future()

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


def _run_coro_on_new_loop(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` on a short-lived loop when no loop is already running."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


@dataclass
class WizardDispatcher:
    router: Router
    audit_logger: object | None = None

    def try_dispatch(
        self, inbound: InboundMessage
    ) -> tuple[JsonDict, ResolvedContext] | None:
        """Try to dispatch to an active wizard session."""
        inbound = canonicalize_inbound_message(inbound)
        wizard_id = ""

        async def async_lookup() -> tuple[JsonDict, ResolvedContext] | None:
            nonlocal wizard_id
            from ...wizard.runtime import get_wizard_executor
            from ...wizard.store import get_wizard_store

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
                return _run_coro_on_new_loop(async_lookup())
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
        session: "WizardSession | None" = None,
    ) -> JsonDict:
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
                "ok": True,
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
            return str(data.get("help_text", "Help information is available."))
        if action == "preview":
            changes_msg = ", ".join(
                [f"{k}={v}" for k, v in data.get("changes", {}).items()]
            )
            return f"Preview of changes: {changes_msg if changes_msg else 'No changes yet.'}"
        if action == "next_step":
            return str(data.get("next_prompt", "Wizard prompt:"))
        if action == "incomplete":
            return "More information needed. Please provide additional details."
        if action == "cancelled":
            return "Wizard process cancelled as requested."
        if getattr(wizard_result, "completed", False):
            return "Wizard completed successfully."
        return "Wizard step processed."

    def _audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)
