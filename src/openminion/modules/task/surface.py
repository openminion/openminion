from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .runtime.lifecycle import TaskLifecycleState


_STATUS_ORDER = {
    "ACTIVE": 0,
    "WAITING": 1,
    "PENDING": 2,
    "DONE": 3,
    "CANCELED": 4,
    "FAILED": 5,
}
_ACTIONS = {"pause", "resume", "cancel", "allow", "deny"}
_ACTION_STATES = {
    "pause": TaskLifecycleState.PAUSED,
    "resume": TaskLifecycleState.ACTIVE,
    "cancel": TaskLifecycleState.CANCELLED,
}


@dataclass(frozen=True)
class TaskSurface:
    """Canonical projection for task inventory surfaces."""

    source: Any | None
    agent_id: str = ""
    session_id: str = ""
    limit: int = 50
    event_limit: int = 500

    def inventory(self) -> dict[str, Any]:
        tasks = self.list_tasks()
        return {
            "ok": True,
            "tasks": tasks,
            "pending_actions": self.list_pending_actions(),
            "count": len(tasks),
            "source": _source_kind(self.source),
        }

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = _tasks_from_digest_source(
            self.source,
            agent_id=self.agent_id,
            session_id=self.session_id,
            limit=self.limit,
            event_limit=self.event_limit,
        )
        if not tasks:
            tasks = _tasks_from_lifecycle_source(self.source, limit=self.limit)
        return sorted(
            tasks,
            key=lambda item: (
                _STATUS_ORDER.get(str(item.get("status", "")).upper(), 9),
                str(item.get("id", "")),
            ),
        )

    def show_task(self, task_id: str) -> dict[str, Any] | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            return None
        for task in self.list_tasks():
            if str(task.get("id", "")) == normalized:
                return task
        return _task_from_lifecycle_record(self.source, normalized)

    def list_pending_actions(self) -> list[dict[str, Any]]:
        _, pending_by_id = _pending_actions_index(
            self.source, event_limit=self.event_limit
        )
        pending = list(pending_by_id.values())
        pending.sort(
            key=lambda item: (
                str(item.get("task_id", "")),
                str(item.get("decision_id", "")),
            )
        )
        return pending

    def apply_action(
        self, *, task_id: str, action: str, decision_id: str = ""
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in _ACTIONS:
            raise ValueError(f"unknown task action: {action!r}")
        if normalized_action in {"allow", "deny"}:
            return _resolve_pending_action(
                self.source,
                outcome=normalized_action,
                decision_id=decision_id,
                session_id=self.session_id,
            )
        return _apply_lifecycle_action(
            self.source,
            task_id=str(task_id or "").strip(),
            action=normalized_action,
        )


def build_task_surface(
    source: Any | None,
    *,
    agent_id: str = "",
    session_id: str = "",
    limit: int = 50,
    event_limit: int = 500,
) -> TaskSurface:
    return TaskSurface(
        source=source,
        agent_id=str(agent_id or "").strip(),
        session_id=str(session_id or "").strip(),
        limit=max(1, int(limit)),
        event_limit=max(1, int(event_limit)),
    )


def resolve_task_surface_source(runtime: Any | None) -> Any | None:
    """Resolve the best task owner without creating a parallel task service."""

    if runtime is None:
        return None
    direct = _first_task_owner(runtime)
    if direct is not None:
        return direct

    for attr in ("agent", "gateway"):
        nested = getattr(runtime, attr, None)
        owner = _first_task_owner(nested)
        if owner is not None:
            return owner

    services = getattr(runtime, "_agent_services", None)
    if isinstance(services, Mapping):
        for service in services.values():
            owner = _first_task_owner(service)
            if owner is not None:
                return owner
    return None


def _first_task_owner(obj: Any | None) -> Any | None:
    if obj is None:
        return None
    for attr in ("task_manager", "task_ctl", "_task_ctl"):
        owner = getattr(obj, attr, None)
        if _looks_like_task_owner(owner):
            return owner
    runner_getter = getattr(obj, "_get_runner", None)
    if callable(runner_getter):
        try:
            runner = runner_getter()
        except (AttributeError, TypeError, ValueError, RuntimeError):
            runner = None
        owner = getattr(runner, "task_manager", None)
        if _looks_like_task_owner(owner):
            return owner
    runner = getattr(obj, "_runner", None)
    owner = getattr(runner, "task_manager", None)
    return owner if _looks_like_task_owner(owner) else None


def _looks_like_task_owner(owner: Any | None) -> bool:
    if owner is None:
        return False
    return any(
        callable(getattr(owner, method, None))
        for method in ("get_digest", "get_task", "list_scheduled_jobs")
    )


def _tasks_from_digest_source(
    source: Any | None,
    *,
    agent_id: str,
    session_id: str,
    limit: int,
    event_limit: int,
) -> list[dict[str, Any]]:
    digest = _safe_get_digest(
        source, agent_id=agent_id, session_id=session_id, limit=limit
    )
    pending_by_task, _ = _pending_actions_index(source, event_limit=event_limit)
    tasks_by_id: dict[str, dict[str, Any]] = {}

    for digest_task in _iter_digest_tasks(digest):
        task_id = str(_value(digest_task, "task_id") or "").strip()
        if not task_id:
            continue
        status = _normalize_status(_value(digest_task, "status", "PENDING"))
        due_at = _normalize_due(_value(digest_task, "due_at"))
        next_step_id = str(_value(digest_task, "next_step_id") or "").strip()
        next_step_title = str(_value(digest_task, "next_step_title") or "").strip()
        steps: list[dict[str, Any]] = []
        if next_step_id or next_step_title:
            steps.append(
                {
                    "order_index": 1,
                    "title": next_step_title or next_step_id,
                    "status": "ACTIVE" if status == "ACTIVE" else "PENDING",
                }
            )

        payload: dict[str, Any] = {
            "id": task_id,
            "title": str(_value(digest_task, "title") or task_id),
            "status": status,
            "due_at": due_at,
            "steps": steps,
            "pending_actions": list(pending_by_task.get(task_id, [])),
        }
        project = _project_payload(_value(digest_task, "metadata", {}))
        if project:
            payload["project"] = project
        tasks_by_id[task_id] = payload

    for task_id, pending_actions in pending_by_task.items():
        tasks_by_id.setdefault(
            task_id,
            {
                "id": task_id,
                "title": f"Task {task_id}",
                "status": "WAITING",
                "steps": [],
                "pending_actions": list(pending_actions),
            },
        )
    return list(tasks_by_id.values())


def _safe_get_digest(
    source: Any | None, *, agent_id: str, session_id: str, limit: int
) -> Any | None:
    get_digest = getattr(source, "get_digest", None)
    if not callable(get_digest):
        return None
    try:
        return get_digest(agent_id=agent_id, session_id=session_id, limit=limit)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None


def _iter_digest_tasks(digest: Any | None) -> list[Any]:
    if digest is None:
        return []
    tasks: list[Any] = []
    for attr in ("tasks_active", "tasks_ready"):
        value = _value(digest, attr, [])
        if isinstance(value, list):
            tasks.extend(value)
    current = _value(digest, "current_task")
    if current is not None:
        tasks.append(current)

    seen: set[str] = set()
    unique: list[Any] = []
    for task in tasks:
        task_id = str(_value(task, "task_id") or "").strip()
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        unique.append(task)
    return unique


def _tasks_from_lifecycle_source(
    source: Any | None, *, limit: int
) -> list[dict[str, Any]]:
    repository = getattr(source, "lifecycle_repository", None)
    list_records = getattr(repository, "list", None)
    if not callable(list_records):
        return []
    try:
        records = list_records(limit=limit)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return []
    return [_lifecycle_record_payload(source, record) for record in records]


def _task_from_lifecycle_record(
    source: Any | None, task_id: str
) -> dict[str, Any] | None:
    get_task = getattr(source, "get_task", None)
    if not callable(get_task):
        return None
    try:
        record = get_task(task_id)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    return _lifecycle_record_payload(source, record) if record is not None else None


def _lifecycle_record_payload(source: Any | None, record: Any) -> dict[str, Any]:
    metadata = dict(_value(record, "metadata", {}) or {})
    task_id = str(_value(record, "task_id") or "").strip()
    title = _task_title(task_id, metadata)
    job = _safe_get_job(source, str(_value(record, "cron_job_id") or task_id).strip())
    due_at = _normalize_due(
        (job or {}).get("next_due_at")
        or metadata.get("due_at")
        or metadata.get("wait_at")
    )
    payload: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "status": _normalize_status(_value(record, "state", "PENDING")),
        "due_at": due_at,
        "steps": _progress_steps(metadata),
        "pending_actions": [],
        "agent_id": _value(record, "agent_id"),
        "cron_job_id": _value(record, "cron_job_id"),
        "created_at": _value(record, "created_at"),
        "updated_at": _value(record, "updated_at"),
    }
    if job is not None:
        payload["schedule"] = job.get("schedule") or job.get("schedule_json")
        payload["enabled"] = bool(job.get("enabled", True))
    project = _project_payload(metadata)
    if project:
        payload["project"] = project
    return payload


def _task_title(task_id: str, metadata: Mapping[str, Any]) -> str:
    for key in ("title", "name", "goal", "instruction", "summary"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return f"Task {task_id}"


def _progress_steps(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    progress = metadata.get("progress")
    if not isinstance(progress, Mapping):
        return []
    checkpoint = str(progress.get("last_checkpoint_id") or "").strip()
    if not checkpoint:
        return []
    return [{"order_index": 1, "title": checkpoint, "status": "ACTIVE"}]


def _safe_get_job(source: Any | None, job_id: str) -> dict[str, Any] | None:
    get_job = getattr(source, "get_scheduled_job", None)
    if not callable(get_job) or not job_id:
        return None
    try:
        job = get_job(job_id)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    return dict(job) if isinstance(job, Mapping) else None


def _pending_actions_index(
    source: Any | None, *, event_limit: int
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    pending_by_id: dict[str, dict[str, Any]] = {}
    pending_by_task: dict[str, list[dict[str, Any]]] = {}
    list_events = getattr(source, "list_events", None)
    if not callable(list_events):
        return pending_by_task, pending_by_id
    try:
        events = list_events()
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return pending_by_task, pending_by_id
    if not isinstance(events, list):
        return pending_by_task, pending_by_id

    for event in events[-event_limit:]:
        event_type = str(
            _value(event, "type") or _value(event, "event_type") or ""
        ).strip()
        payload = _value(event, "payload", {})
        if not isinstance(payload, dict):
            payload = {}
        task_id = str(_value(event, "task_id") or payload.get("task_id") or "").strip()
        policy_request_id = str(payload.get("policy_request_id") or "").strip()
        if event_type == "mission.paused" and policy_request_id:
            action = {
                "decision_id": policy_request_id,
                "reason": str(payload.get("reason") or "").strip(),
                "tool": str(payload.get("tool") or "").strip(),
                "task_id": task_id,
            }
            pending_by_id[policy_request_id] = action
            if task_id:
                pending_by_task.setdefault(task_id, []).append(action)
            continue
        if event_type == "mission.resumed" and policy_request_id:
            removed = pending_by_id.pop(policy_request_id, None)
            if removed is None:
                continue
            removed_task_id = str(removed.get("task_id", ""))
            if removed_task_id and removed_task_id in pending_by_task:
                pending_by_task[removed_task_id] = [
                    item
                    for item in pending_by_task[removed_task_id]
                    if str(item.get("decision_id", "")) != policy_request_id
                ]
                if not pending_by_task[removed_task_id]:
                    pending_by_task.pop(removed_task_id, None)
    return pending_by_task, pending_by_id


def _resolve_pending_action(
    source: Any | None, *, outcome: str, decision_id: str, session_id: str
) -> dict[str, Any]:
    decision = str(decision_id or "").strip()
    if not decision:
        raise ValueError("decision_id is required for allow/deny")
    resume_pending_action = getattr(source, "resume_pending_action", None)
    if not callable(resume_pending_action):
        raise NotImplementedError("pending action resolution is unavailable")
    resume_pending_action(
        policy_request_id=decision,
        decision_id=f"task-surface:{outcome}:{decision}",
        trace_id=f"task-surface:{session_id}",
    )
    return {"ok": True, "action": outcome, "decision_id": decision}


def _apply_lifecycle_action(
    source: Any | None, *, task_id: str, action: str
) -> dict[str, Any]:
    if not task_id:
        raise ValueError("task_id is required")
    method_name = {
        "pause": "pause_task",
        "resume": "resume_task",
        "cancel": "cancel_task",
    }[action]
    method = getattr(source, method_name, None)
    if not callable(method):
        record = _transition_task(source, task_id=task_id, action=action)
        return _task_action_result(source, action=action, record=record)
    try:
        result = method(task_id)
    except KeyError as exc:
        record = _transition_task(
            source, task_id=task_id, action=action, missing_exc=exc
        )
        return _task_action_result(source, action=action, record=record)
    record = result[0] if isinstance(result, tuple) else result
    return _task_action_result(source, action=action, record=record)


def _transition_task(
    source: Any | None,
    *,
    task_id: str,
    action: str,
    missing_exc: Exception | None = None,
) -> Any:
    transition = getattr(source, "transition_task", None)
    if not callable(transition):
        if missing_exc is not None:
            raise missing_exc
        raise NotImplementedError(f"task {action} is unavailable")
    return transition(task_id=task_id, to_state=_ACTION_STATES[action])


def _task_action_result(
    source: Any | None, *, action: str, record: Any
) -> dict[str, Any]:
    return {
        "ok": True,
        "action": action,
        "task": _lifecycle_record_payload(source, record),
    }


def _value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_due(raw_due: Any) -> str | None:
    if raw_due is None:
        return None
    if isinstance(raw_due, datetime):
        return raw_due.date().isoformat()
    text = str(raw_due).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text


def _normalize_status(raw_status: Any) -> str:
    value = getattr(raw_status, "value", raw_status)
    text = str(value or "PENDING").strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    text = text.upper()
    if text == "CANCELLED":
        text = "CANCELED"
    if text == "PAUSED":
        text = "WAITING"
    return text if text in _STATUS_ORDER else "PENDING"


def _project_payload(metadata: Any) -> dict[str, str] | None:
    if not isinstance(metadata, Mapping):
        return None
    project_run_id = str(metadata.get("project_run_id") or "").strip()
    if not project_run_id:
        return None
    return {
        "project_run_id": project_run_id,
        "autonomy_run_id": str(metadata.get("autonomy_run_id") or "").strip(),
        "goal_id": str(metadata.get("goal_id") or "").strip(),
        "phase": str(metadata.get("project_phase") or "").strip(),
        "verification": str(metadata.get("verification_state") or "").strip(),
        "checkpoint": str(metadata.get("last_checkpoint_id") or "").strip(),
    }


def _source_kind(source: Any | None) -> str:
    if source is None:
        return "unavailable"
    if callable(getattr(source, "get_digest", None)):
        return "task_ctl"
    if callable(getattr(source, "list_scheduled_jobs", None)):
        return "task_manager"
    return type(source).__name__
