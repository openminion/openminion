import logging
from typing import Any

_log = logging.getLogger(__name__)

PLAN_REVIEW_REQUESTED_EVENT = "brain.plan_review.requested"
PLAN_REVIEW_RESOLVED_EVENT = "brain.plan_review.resolved"

_EVENT_PREFIX_ACTOR: dict[str, str] = {
    "llm.": "agent",
    "tool.": "tool",
    "job.": "system",
    "task.": "agent",
    "task_plan.": "agent",
    "agent.": "system",
    "a2a.": "system",
    "turn.user": "user",
    "turn.assistant": "agent",
    "turn.system": "system",
    "turn.tool": "tool",
    "policy.": "system",
    "safety.": "system",
    "memory.": "system",
    "context.": "system",
    "session.": "system",
    "selection.": "system",
    "skill.": "agent",
    "plan.": "agent",
    "brain.": "agent",
    "trailer.": "system",
}

_EVENT_PREFIX_IMPORTANCE: dict[str, int] = {
    "llm.": 1,
    "tool.": 2,
    "job.": 1,
    "task.": 2,
    "task_plan.": 2,
    "agent.": 2,
    "a2a.": 2,
    "turn.": 3,
    "policy.": 1,
    "safety.": 3,
    "context.": 0,
    "session.": 1,
    "selection.": 1,
    "skill.": 1,
    "plan.": 1,
    "brain.": 2,
    "trailer.": 1,
}


def derive_actor_type(event_type: str) -> str:
    for prefix, actor_type in _EVENT_PREFIX_ACTOR.items():
        if event_type.startswith(prefix):
            return actor_type
    return "system"


def derive_importance(event_type: str) -> int:
    for prefix, importance in _EVENT_PREFIX_IMPORTANCE.items():
        if event_type.startswith(prefix):
            return importance
    return 1


class CanonicalEventLogger:
    """Append canonical session events with brain-local enrichment defaults."""

    def __init__(
        self,
        *,
        session_api: Any,
        session_id: str,
        agent_id: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self._session_api = session_api
        self._session_id = session_id
        self._agent_id = agent_id
        self._log = logger or _log

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
        task_id: str | None = None,
        parent_id: str | None = None,
        artifact_refs: list[str] | None = None,
        memory_refs: list[str] | None = None,
        status: str | None = None,
        error: dict[str, Any] | None = None,
        span_id: str | None = None,
        importance: int | None = None,
        redaction: str | None = None,
    ) -> str:
        """Emit a canonical event with enriched actor_type and importance fields."""
        actor_type = derive_actor_type(event_type)
        derived_importance = (
            importance if importance is not None else derive_importance(event_type)
        )

        return self._session_api.append_event(
            self._session_id,
            event_type,
            payload,
            actor_type=actor_type,
            actor_id=self._agent_id,
            trace={"trace_id": trace_id, "span_id": span_id} if trace_id else None,
            importance=derived_importance,
            redaction=redaction or "none",
            trace_id=trace_id,
            task_id=task_id,
            parent_id=parent_id,
            artifact_refs=artifact_refs,
            memory_refs=memory_refs,
            status=status,
            error=error,
        )


__all__ = [
    "CanonicalEventLogger",
    "PLAN_REVIEW_REQUESTED_EVENT",
    "PLAN_REVIEW_RESOLVED_EVENT",
    "derive_actor_type",
    "derive_importance",
]
