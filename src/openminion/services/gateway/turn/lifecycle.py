import logging
from typing import Any, Optional
from collections.abc import Callable

from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.services.gateway.turn.runtime import _correlation_payload
from openminion.modules.task.run import Run, append_lifecycle_event
from openminion.services.brain.adapters.run_verification import bind_run_terminal_event


class _GatewayTurnLifecycleOps:
    def __init__(
        self,
        *,
        sessions: SessionStore,
        logger: logging.Logger,
        emit_run_state: Callable[..., None],
        typed_terminal_resolver: Optional[
            Callable[..., Optional[tuple[Any, ...]]]
        ] = None,
    ) -> None:
        self._sessions = sessions
        self._logger = logger
        self._emit_run_state = emit_run_state
        self._typed_terminal_resolver = typed_terminal_resolver

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
    ) -> None:
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

    def emit_terminal_run_state(
        self,
        *,
        session_id: str,
        run_id: str,
        legacy_state: str,
        current_step: str,
        payload: Optional[dict[str, Any]] = None,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        attach_id: str | None = None,
        typed_terminal_resolver: Optional[
            Callable[..., Optional[tuple[Any, ...]]]
        ] = None,
        session_turn_fence_token: int | None = None,
    ) -> None:
        resolver = typed_terminal_resolver or self._typed_terminal_resolver
        if resolver is not None:
            try:
                resolved = resolver(
                    run_id=run_id,
                    session_id=session_id,
                    legacy_state=legacy_state,
                )
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
                        bind_run_terminal_event(
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
                        )
                        return
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
        self._emit_run_state(**kwargs)

    @staticmethod
    def corr_payload(
        *,
        normalized_request_id: str,
        lifecycle_payload: dict[str, Any],
        extra: dict[str, Any],
    ) -> dict[str, Any]:
        return _correlation_payload(
            request_id=normalized_request_id,
            payload={**extra, **lifecycle_payload},
        )

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
