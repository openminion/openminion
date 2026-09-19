from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Coroutine, Literal, cast
from uuid import uuid4

from openminion.api.operations.session_continuations import (
    resolve_session_continuation_store,
)
from openminion.base.redaction import redact_sensitive_text
from openminion.modules.brain.schemas.decisions import DelegationResultSummary
from openminion.modules.session import SessionContinuationService
from openminion.modules.session.schemas import RoomHandoffResultV1
from openminion.modules.telemetry.trace import phase_timing
from openminion.services.gateway.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)


class RuntimeRoomTaskMixin:
    _channel: str
    _rt: Any
    _target: str

    if TYPE_CHECKING:

        def _room_owner(self) -> tuple[Any, Any]: ...

        def _wrap_progress_callback(
            self, callback: Callable[[dict[str, Any]], None] | None
        ) -> Callable[[dict[str, Any]], None]: ...

        def _begin_turn_usage_tracking(self) -> None: ...

        def _finalize_turn_usage(
            self, metadata: Mapping[str, Any] | None, *, succeeded: bool
        ) -> None: ...

        def _record_chat_phase_timing(
            self, timer: phase_timing.ChatPhaseTimer, *, turn_id: str
        ) -> None: ...

    async def start_room_task(
        self,
        task_step_id: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        approval_callback: Callable[[str, dict[str, Any], Any], Awaitable[bool]]
        | None = None,
        cancel_event: Any,
    ) -> dict[str, object]:
        session, _actor = self._room_owner()
        store = resolve_session_continuation_store(self._rt)
        owned_store = getattr(self._rt, "session_continuation_store", None) is None
        try:
            plan = store.get_active_task_plan(session.id)
            steps = list(plan.get("steps", [])) if plan else []
            step = next(
                (item for item in steps if item.get("step_id") == task_step_id),
                None,
            )
            if step is None or step.get("status") not in {"pending", "in_progress"}:
                raise ValueError("room task is not active")
            target_agent = str(step.get("assigned_participant_id") or "")
            worker_session = str(step.get("worker_session_id") or "")
            packet_id = str(step.get("continuation_packet_id") or "")
            if not target_agent or not worker_session or not packet_id:
                raise ValueError("room task has no applied handoff")
            packet = SessionContinuationService(store).get_packet(packet_id)
            binding = packet.payload.room_handoff_binding
            if binding is None:
                raise ValueError("room task handoff packet is invalid")
            instruction = str(step.get("description") or "").strip()
        finally:
            if owned_store:
                store.close()
        if self._rt.sessions.get_participant(session.id, "agent", target_agent) is None:
            raise ValueError("room task agent is no longer an active participant")

        result = await self._run_off_loop_turn(
            {
                "message": instruction,
                "agent_id": target_agent,
                "session_id": worker_session,
                "channel": self._channel,
                "target": self._target,
                "inbound_metadata": {
                    CALLER_HANDLES_DELIVERY_METADATA_KEY: "true",
                    "delegation_context_summary": instruction,
                    "delegation_context_intent_id": task_step_id,
                },
                "deliver": False,
            },
            progress_callback=progress_callback,
            approval_callback=approval_callback,
            cancel_event=cancel_event,
        )
        handback = self._accept_room_task_result(
            result,
            packet_id=packet_id,
            room_session_id=session.id,
            worker_session_id=worker_session,
            source_agent_id=binding.source_agent_id,
            target_agent_id=target_agent,
        )
        return {**result, "room_handback": handback}

    async def _run_off_loop_turn(
        self,
        payload: dict[str, object],
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        approval_callback: Callable[[str, dict[str, Any], Any], Awaitable[bool]] | None,
        cancel_event: Any,
    ) -> dict[str, object]:
        loop = asyncio.get_running_loop()
        wrapped_progress = self._wrap_progress_callback(progress_callback)

        def progress_from_worker(value: object) -> None:
            if isinstance(value, Mapping):
                mapped = dict(value)
            else:
                model_dump = getattr(value, "model_dump", None)
                mapped = dict(model_dump(mode="json")) if callable(model_dump) else {}
            if mapped:
                loop.call_soon_threadsafe(wrapped_progress, mapped)

        approval_from_worker = None
        if approval_callback is not None:

            def approval_from_worker(
                tool_name: str, args: dict[str, Any], call_id: Any
            ) -> bool:
                return bool(
                    asyncio.run_coroutine_threadsafe(
                        cast(
                            Coroutine[Any, Any, bool],
                            approval_callback(tool_name, args, call_id),
                        ),
                        loop,
                    ).result()
                )

        timer = phase_timing.ChatPhaseTimer(cold_start=False)
        result: dict[str, object] | None = None
        succeeded = False
        self._begin_turn_usage_tracking()
        try:
            with phase_timing.use_chat_phase_timer(timer):
                result = cast(
                    dict[str, object],
                    await asyncio.to_thread(
                        self._rt.run_turn,
                        payload=payload,
                        progress_callback=progress_from_worker,
                        approval_callback=approval_from_worker,
                        cancel_event=cancel_event,
                    ),
                )
            succeeded = True
            if str(result.get("body", "") or "").strip():
                phase_timing.mark_active_chat_first_text()
            return result
        finally:
            metadata = result.get("metadata") if result is not None else None
            self._finalize_turn_usage(
                metadata if isinstance(metadata, Mapping) else None,
                succeeded=succeeded,
            )
            self._record_chat_phase_timing(timer, turn_id=uuid4().hex)

    def _accept_room_task_result(
        self,
        result: Mapping[str, object],
        *,
        packet_id: str,
        room_session_id: str,
        worker_session_id: str,
        source_agent_id: str,
        target_agent_id: str,
    ) -> dict[str, Any]:
        metadata = result.get("metadata")
        raw_summary = (
            metadata.get("delegation_result_summary")
            if isinstance(metadata, Mapping)
            else None
        )
        if isinstance(raw_summary, str):
            raw_summary = json.loads(raw_summary)
        summary = DelegationResultSummary.model_validate(raw_summary)
        statuses: dict[
            str, Literal["completed", "failed", "cancelled", "needs_human"]
        ] = {
            "complete": "completed",
            "failed": "failed",
            "partial": "needs_human",
            "blocked": "needs_human",
        }
        safe_summary, _ = redact_sensitive_text(summary.summary)
        handback = RoomHandoffResultV1(
            handoff_packet_id=packet_id,
            source_room_session_id=room_session_id,
            worker_session_id=worker_session_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            status=statuses[summary.status],
            summary=safe_summary,
            artifact_refs=summary.artifacts_produced,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        store = resolve_session_continuation_store(self._rt)
        owned_store = getattr(self._rt, "session_continuation_store", None) is None
        try:
            accepted = SessionContinuationService(store).accept_room_handback(handback)
        finally:
            if owned_store:
                store.close()
        return cast(dict[str, Any], accepted.model_dump(mode="json"))
