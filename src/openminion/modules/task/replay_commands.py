from __future__ import annotations

from dataclasses import dataclass, field
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from openminion.modules.runtime.replay import (
    ReplayBundle,
    ReplayResult,
    ReplayUseCase,
    default_policy_for,
    replay_from_events,
)

from .runtime.lifecycle import TaskLifecycleRecord, TaskManager

ReplayCommandAction = Literal["checkpoint", "replay", "rewind", "branch", "compare"]
BranchMode = Literal["before_checkpoint", "from_checkpoint"]
_SUPPORTED_BRANCH_MODES: tuple[BranchMode, ...] = (
    "before_checkpoint",
    "from_checkpoint",
)


@dataclass(frozen=True)
class ReplayCommandResult:
    """User-facing replay/checkpoint command envelope."""

    ok: bool
    action: ReplayCommandAction
    task_id: str
    checkpoint_id: str | None = None
    branch_task_id: str | None = None
    source_refs: tuple[str, ...] = ()
    events_replayed: int = 0
    events_skipped: int = 0
    divergences: tuple[dict[str, Any], ...] = ()
    checkpoints: tuple[str, ...] = ()
    nondeterminism_notes: tuple[str, ...] = ()
    error: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "task_id": self.task_id,
            "checkpoint_id": self.checkpoint_id,
            "branch_task_id": self.branch_task_id,
            "source_refs": list(self.source_refs),
            "events_replayed": self.events_replayed,
            "events_skipped": self.events_skipped,
            "divergences": list(self.divergences),
            "checkpoints": list(self.checkpoints),
            "nondeterminism_notes": list(self.nondeterminism_notes),
            "error": self.error,
            "metadata": dict(self.metadata),
        }


def list_task_checkpoints(
    task_manager: TaskManager,
    *,
    task_id: str,
) -> ReplayCommandResult:
    record = _require_task(task_manager, task_id)
    checkpoints = tuple(task_manager.list_checkpoints(record.task_id))
    return ReplayCommandResult(
        ok=True,
        action="checkpoint",
        task_id=record.task_id,
        checkpoints=checkpoints,
        source_refs=_task_source_refs(record),
    )


def replay_task_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
    checkpoint_id: str | None = None,
    use_case: ReplayUseCase = "debug",
) -> ReplayCommandResult:
    record = _require_task(task_manager, task_id)
    resolved_checkpoint_id, state = _load_checkpoint(
        task_manager,
        task_id=record.task_id,
        checkpoint_id=checkpoint_id,
    )
    replay_result = replay_from_events(
        _bundle_from_checkpoint(
            task_id=record.task_id,
            checkpoint_id=resolved_checkpoint_id,
            state=state,
            use_case=use_case,
        )
    )
    return _result_from_replay(
        action="replay",
        record=record,
        checkpoint_id=resolved_checkpoint_id,
        replay_result=replay_result,
    )


def compare_task_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
    checkpoint_id: str | None = None,
    expected_checkpoint_id: str | None = None,
) -> ReplayCommandResult:
    record = _require_task(task_manager, task_id)
    resolved_checkpoint_id, state = _load_checkpoint(
        task_manager,
        task_id=record.task_id,
        checkpoint_id=checkpoint_id,
    )
    _expected_id, expected_state = _load_checkpoint(
        task_manager,
        task_id=record.task_id,
        checkpoint_id=expected_checkpoint_id,
    )
    bundle = _bundle_from_checkpoint(
        task_id=record.task_id,
        checkpoint_id=resolved_checkpoint_id,
        state=state,
        use_case="regression_test",
        expected_state=expected_state,
    )
    replay_result = replay_from_events(bundle)
    return _result_from_replay(
        action="compare",
        record=record,
        checkpoint_id=resolved_checkpoint_id,
        replay_result=replay_result,
    )


def branch_task_from_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
    checkpoint_id: str,
    branch_mode: BranchMode = "from_checkpoint",
    branch_task_id: str | None = None,
) -> ReplayCommandResult:
    if branch_mode not in _SUPPORTED_BRANCH_MODES:
        raise ValueError(f"unsupported branch mode: {branch_mode}")
    record = _require_task(task_manager, task_id)
    resolved_checkpoint_id, state = _load_checkpoint(
        task_manager,
        task_id=record.task_id,
        checkpoint_id=checkpoint_id,
    )
    branch_record = _create_branch_record(
        task_manager,
        source=record,
        checkpoint_id=resolved_checkpoint_id,
        checkpoint_state=state,
        branch_mode=branch_mode,
        branch_task_id=branch_task_id,
        action="branch",
    )
    return ReplayCommandResult(
        ok=True,
        action="branch",
        task_id=record.task_id,
        checkpoint_id=resolved_checkpoint_id,
        branch_task_id=branch_record.task_id,
        source_refs=_branch_source_refs(record.task_id, resolved_checkpoint_id),
        metadata={"branch_mode": branch_mode},
    )


def rewind_task_to_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
    checkpoint_id: str,
    branch_task_id: str | None = None,
) -> ReplayCommandResult:
    record = _require_task(task_manager, task_id)
    resolved_checkpoint_id, state = _load_checkpoint(
        task_manager,
        task_id=record.task_id,
        checkpoint_id=checkpoint_id,
    )
    branch_record = _create_branch_record(
        task_manager,
        source=record,
        checkpoint_id=resolved_checkpoint_id,
        checkpoint_state=state,
        branch_mode="before_checkpoint",
        branch_task_id=branch_task_id,
        action="rewind",
    )
    return ReplayCommandResult(
        ok=True,
        action="rewind",
        task_id=record.task_id,
        checkpoint_id=resolved_checkpoint_id,
        branch_task_id=branch_record.task_id,
        source_refs=_branch_source_refs(record.task_id, resolved_checkpoint_id),
        metadata={"branch_mode": "before_checkpoint"},
    )


def _require_task(task_manager: TaskManager, task_id: str) -> TaskLifecycleRecord:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        raise ValueError("task_id is required")
    record = task_manager.get_task(normalized_task_id)
    if record is None:
        raise KeyError(f"task not found: {normalized_task_id}")
    return record


def _load_checkpoint(
    task_manager: TaskManager,
    *,
    task_id: str,
    checkpoint_id: str | None,
) -> tuple[str, dict[str, Any]]:
    normalized_checkpoint_id = str(checkpoint_id or "").strip()
    if normalized_checkpoint_id:
        state = task_manager.get_checkpoint(task_id, normalized_checkpoint_id)
        if state is None:
            raise KeyError(f"checkpoint not found: {task_id}:{normalized_checkpoint_id}")
        return normalized_checkpoint_id, dict(state)
    latest = task_manager.get_latest_checkpoint(task_id)
    if latest is None:
        raise KeyError(f"checkpoint not found: {task_id}")
    latest_checkpoint_id, state = latest
    return latest_checkpoint_id, dict(state)


def _bundle_from_checkpoint(
    *,
    task_id: str,
    checkpoint_id: str,
    state: Mapping[str, Any],
    use_case: ReplayUseCase,
    expected_state: Mapping[str, Any] | None = None,
) -> ReplayBundle:
    return ReplayBundle(
        use_case=use_case,
        initial_state=dict(state.get("initial_state") or state),
        event_log=_event_log_from_state(state),
        policy=default_policy_for(use_case),
        bundle_id=f"{task_id}:{checkpoint_id}",
        recorded_at=_checkpoint_recorded_at(state),
        expected_state=expected_state,
        expected_event_payloads=_expected_payloads_from_state(state),
    )


def _event_log_from_state(state: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = state.get("event_log")
    if isinstance(raw, list | tuple):
        return tuple(item for item in raw if isinstance(item, Mapping))
    return ()


def _expected_payloads_from_state(state: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    raw = state.get("expected_event_payloads")
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(value, Mapping)
    }


def _checkpoint_recorded_at(state: Mapping[str, Any]) -> datetime:
    raw = state.get("recorded_at") or state.get("created_at")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _result_from_replay(
    *,
    action: Literal["replay", "compare"],
    record: TaskLifecycleRecord,
    checkpoint_id: str,
    replay_result: ReplayResult,
) -> ReplayCommandResult:
    return ReplayCommandResult(
        ok=not replay_result.divergences,
        action=action,
        task_id=record.task_id,
        checkpoint_id=checkpoint_id,
        source_refs=_branch_source_refs(record.task_id, checkpoint_id),
        events_replayed=replay_result.events_replayed,
        events_skipped=replay_result.events_skipped,
        divergences=tuple(_divergence_dict(item) for item in replay_result.divergences),
        nondeterminism_notes=(
            "replay uses recorded event payloads only; providers and tools are not re-invoked"
        ,),
        metadata={"state": dict(replay_result.final_state)},
    )


def _divergence_dict(divergence: Any) -> dict[str, Any]:
    return {
        "event_id": str(divergence.event_id),
        "seam_id": str(divergence.seam_id),
        "divergence_kind": str(divergence.divergence_kind),
        "expected_payload": dict(divergence.expected_payload),
        "actual_payload": dict(divergence.actual_payload),
        "recorded_at": divergence.recorded_at.isoformat(),
    }


def _create_branch_record(
    task_manager: TaskManager,
    *,
    source: TaskLifecycleRecord,
    checkpoint_id: str,
    checkpoint_state: Mapping[str, Any],
    branch_mode: BranchMode,
    branch_task_id: str | None,
    action: Literal["branch", "rewind"],
) -> TaskLifecycleRecord:
    metadata = dict(source.metadata)
    metadata.update(
        {
            "kind": "replay_branch",
            "replay_action": action,
            "source_task_id": source.task_id,
            "source_checkpoint_id": checkpoint_id,
            "branch_mode": branch_mode,
            "checkpoint_state": dict(checkpoint_state),
        }
    )
    requested_task_id = str(branch_task_id or "").strip() or None
    linked_job_id = requested_task_id or (
        f"{source.task_id}:{action}:{checkpoint_id}:{uuid.uuid4().hex[:12]}"
    )
    return task_manager.create_linked_task(
        linked_job_id=linked_job_id,
        agent_id=source.agent_id,
        metadata=metadata,
        task_id=requested_task_id,
    )


def _task_source_refs(record: TaskLifecycleRecord) -> tuple[str, ...]:
    return (f"task:{record.task_id}",)


def _branch_source_refs(task_id: str, checkpoint_id: str) -> tuple[str, ...]:
    return (f"task:{task_id}", f"checkpoint:{task_id}:{checkpoint_id}")


__all__ = [
    "BranchMode",
    "ReplayCommandAction",
    "ReplayCommandResult",
    "branch_task_from_checkpoint",
    "compare_task_checkpoint",
    "list_task_checkpoints",
    "replay_task_checkpoint",
    "rewind_task_to_checkpoint",
]
