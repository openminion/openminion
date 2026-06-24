from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .contracts import CheckpointConsumer, CheckpointEnvelope


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


class CheckpointManager:
    def __init__(self, *, task_service: Any) -> None:
        self._task_service = task_service

    def _owner_for(self, consumer: CheckpointConsumer) -> str:
        owner = _normalized_text(getattr(consumer, "mode_name", ""))
        return owner or consumer.__class__.__name__.lower()

    def _checkpoint_id(self, *, owner: str, task_id: str, cursor: int) -> str:
        return f"{owner}-{task_id}-cursor-{int(cursor)}"

    def latest_raw_checkpoint(
        self, *, task_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        normalized_task_id = _normalized_text(task_id)
        if not normalized_task_id:
            return None
        return self._task_service.get_latest_checkpoint(normalized_task_id)

    def load_envelope(self, *, task_id: str) -> CheckpointEnvelope | None:
        latest = self.latest_raw_checkpoint(task_id=task_id)
        if latest is None:
            return None
        _, raw_state = latest
        try:
            return CheckpointEnvelope.model_validate(raw_state)
        except Exception:
            return None

    def save(
        self,
        *,
        consumer: CheckpointConsumer,
        task_id: str,
        cursor: int = 0,
    ) -> str:
        return self.save_payload(
            owner=self._owner_for(consumer),
            version=int(getattr(consumer, "CHECKPOINT_VERSION", 1) or 1),
            task_id=task_id,
            payload=consumer.snapshot_state(),
            cursor=cursor,
        )

    def save_payload(
        self,
        *,
        owner: str,
        version: int,
        task_id: str,
        payload: dict[str, Any],
        cursor: int = 0,
    ) -> str:
        normalized_owner = _normalized_text(owner)
        normalized_task_id = _normalized_text(task_id)
        if not normalized_owner:
            raise ValueError("owner is required")
        if not normalized_task_id:
            raise ValueError("task_id is required")
        checkpoint_id = self._checkpoint_id(
            owner=normalized_owner,
            task_id=normalized_task_id,
            cursor=max(0, int(cursor)),
        )
        envelope = CheckpointEnvelope(
            version=max(1, int(version)),
            owner=normalized_owner,
            cursor=max(0, int(cursor)),
            timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            payload=dict(payload or {}),
        )
        self._task_service.save_checkpoint(
            normalized_task_id,
            checkpoint_id,
            envelope.model_dump(mode="json"),
        )
        return checkpoint_id

    def load(
        self,
        *,
        consumer: CheckpointConsumer,
        task_id: str,
    ) -> CheckpointEnvelope | None:
        envelope = self.load_envelope(task_id=task_id)
        if envelope is None:
            return None
        if int(envelope.version) != int(
            getattr(consumer, "CHECKPOINT_VERSION", 1) or 1
        ):
            return None
        consumer.restore_state(dict(envelope.payload))
        return envelope

    def create_task(
        self,
        *,
        session_id: str,
        owner: str,
        goal: str,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        record = self._task_service.create_task(
            session_id=_normalized_text(session_id),
            mode_name=_normalized_text(owner),
            goal=_normalized_text(goal),
            agent_id=agent_id,
            metadata=metadata,
        )
        return _normalized_text(getattr(record, "task_id", ""))

    def transition_task(
        self,
        *,
        task_id: str,
        to_state: str,
        failure_reason: str | None = None,
    ) -> None:
        self._task_service.transition_task(
            task_id=_normalized_text(task_id),
            to_state=_normalized_text(to_state),
            failure_reason=failure_reason,
        )


__all__ = ["CheckpointManager"]
