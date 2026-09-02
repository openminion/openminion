import logging
from datetime import datetime
from typing import Any
from collections.abc import Callable

from openminion.modules.storage.runtime.session_store import (
    EventRecord,
    RuntimeSessionTurnFenceError as RuntimeSessionTurnFenceError,
    SessionStore,
)
from openminion.modules.task.run import (
    Run,
    append_lifecycle_event,
)
from openminion.modules.task.run.status import resolve_invocation_terminal
from openminion.services.brain.adapters.run_verification import bind_run_terminal_event
from openminion.services.agent.telemetry import InvocationLifecycleFact


class _GatewayTurnLifecycleOps:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        logger: logging.Logger,
        emit_run_state: Callable[..., EventRecord | None],
        emit_invocation_lifecycle: Callable[[InvocationLifecycleFact], bool] | None,
        typed_terminal_resolver: Callable[..., tuple[Any, ...] | None] | None = None,
    ) -> None:
        self._sessions = sessions
        self._logger = logger
        self._emit_run_state = emit_run_state
        self._emit_invocation_lifecycle = emit_invocation_lifecycle
        self._typed_terminal_resolver = typed_terminal_resolver

    def emit_invocation_lifecycle(self, fact: InvocationLifecycleFact) -> bool:
        return bool(
            self._emit_invocation_lifecycle and self._emit_invocation_lifecycle(fact)
        )

    def emit_memory_event(
        self,
        *,
        session_id: str,
        event_type: str,
        conversation_id: str | None,
        thread_id: str | None,
        attach_id: str | None,
        payload: dict[str, str],
        session_turn_fence_token: int | None = None,
    ) -> None:
        try:
            append_lifecycle_event(
                self._sessions,
                session_id=session_id,
                event_type=event_type,
                conversation_id=conversation_id,
                thread_id=thread_id,
                attach_id=attach_id,
                payload=payload,
                session_turn_fence_token=session_turn_fence_token,
            )
        except RuntimeSessionTurnFenceError:
            raise
        except Exception as exc:
            self._logger.debug(
                "agent memory event append failed session_id=%s event_type=%s error=%s",
                session_id,
                event_type,
                exc,
            )

    def emit_turn_event(
        self,
        *,
        session_id: str,
        event_type: str,
        conversation_id: str | None,
        thread_id: str | None,
        attach_id: str | None,
        payload: dict[str, str],
        session_turn_fence_token: int | None = None,
    ) -> EventRecord:
        record = append_lifecycle_event(
            self._sessions,
            session_id=session_id,
            event_type=event_type,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            payload=payload,
            session_turn_fence_token=session_turn_fence_token,
        )
        if event_type in {"response.delivered", "response.acked"}:
            self._emit_resolved_invocation_terminal(
                record,
                conversation_id=conversation_id or "",
                thread_id=thread_id or "",
            )
        return record

    def emit_terminal_run_state(
        self,
        *,
        session_id: str,
        run_id: str,
        legacy_state: str,
        current_step: str,
        payload: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        attach_id: str | None = None,
        typed_terminal_resolver: Callable[..., tuple[Any, ...] | None] | None = None,
        session_turn_fence_token: int | None = None,
    ) -> EventRecord | None:
        resolver = typed_terminal_resolver or self._typed_terminal_resolver
        if resolver is not None:
            try:
                resolved = resolver(
                    run_id=run_id,
                    session_id=session_id,
                    legacy_state=legacy_state,
                )
            except RuntimeSessionTurnFenceError:
                raise
            except Exception as exc:
                self._logger.warning(
                    "typed_terminal_resolver failed run_id=%s error=%s; "
                    "falling back to legacy run-state emission",
                    run_id,
                    exc,
                )
                resolved = None
            if resolved is not None:
                run, goal, verifier_results, fired_failure_conditions = resolved
                if not isinstance(run, Run):
                    self._logger.warning(
                        "typed_terminal_resolver returned non-Run for run_id=%s; "
                        "falling back to legacy run-state emission",
                        run_id,
                    )
                else:
                    try:
                        terminal_event = bind_run_terminal_event(
                            run=run,
                            goal=goal,
                            verifier_results=verifier_results,
                            sessions=self._sessions,
                            fired_failure_conditions=fired_failure_conditions,
                            checkpoint_id=f"{run.run_id}:terminal",
                            current_step=current_step,
                            conversation_id=conversation_id,
                            thread_id=thread_id,
                            attach_id=attach_id,
                            extra_payload=dict(payload or {}),
                            session_turn_fence_token=session_turn_fence_token,
                        )
                        self._emit_resolved_invocation_terminal(
                            terminal_event,
                            conversation_id=conversation_id or "",
                            thread_id=thread_id or "",
                        )
                        return terminal_event
                    except RuntimeSessionTurnFenceError:
                        raise
                    except Exception as exc:
                        self._logger.warning(
                            "bind_run_terminal_event failed run_id=%s error=%s; "
                            "falling back to legacy run-state emission",
                            run_id,
                            exc,
                        )

        kwargs: dict[str, Any] = {
            "session_id": session_id,
            "run_id": run_id,
            "state": legacy_state,
            "current_step": current_step,
            "payload": payload,
        }
        if session_turn_fence_token is not None:
            kwargs["session_turn_fence_token"] = session_turn_fence_token
        terminal_event = self._emit_run_state(**kwargs)
        if terminal_event is not None:
            self._emit_resolved_invocation_terminal(
                terminal_event,
                conversation_id=conversation_id or "",
                thread_id=thread_id or "",
            )
        return terminal_event

    def _emit_resolved_invocation_terminal(
        self,
        source: EventRecord,
        *,
        conversation_id: str,
        thread_id: str,
    ) -> bool:
        projection = resolve_invocation_terminal(
            self._sessions,
            session_id=source.session_id,
            trigger_event=source,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        if projection is None:
            return False
        event_types = {
            "settled": "agent.invocation.completed",
            "failed": "agent.invocation.failed",
            "cancelled": "agent.invocation.cancelled",
        }
        return self.emit_invocation_lifecycle(
            InvocationLifecycleFact(
                event_id=f"agent.invocation:{projection.invocation_id}:terminal",
                timestamp=datetime.fromisoformat(
                    projection.source_timestamp
                ).timestamp(),
                event_type=event_types[projection.resolved_state],
                invocation_id=projection.invocation_id,
                session_id=source.session_id,
                turn_id=str(source.payload.get("request_id") or projection.run_id),
                payload={
                    "scope": "durable",
                    "source_event_id": projection.source_event_id,
                    "source_event_type": projection.source_event_type,
                    "resolved_state": projection.resolved_state,
                    "run_id": projection.run_id,
                    "thread_id": projection.thread_id,
                    "provider": source.payload.get("provider") or None,
                    "model": source.payload.get("model") or None,
                    "error_code": source.payload.get("error_code") or None,
                },
            )
        )

    @staticmethod
    def corr_payload(
        *,
        normalized_request_id: str,
        lifecycle_payload: dict[str, Any],
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {**extra, **lifecycle_payload}
        if normalized_request_id:
            payload["request_id"] = normalized_request_id
        return payload

    @staticmethod
    def optional_ids(
        conversation_id: str | None,
        thread_id: str | None,
        attach_id: str | None,
    ) -> dict[str, str]:
        payload: dict[str, str] = {}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if thread_id:
            payload["thread_id"] = thread_id
        if attach_id:
            payload["attach_id"] = attach_id
        return payload
