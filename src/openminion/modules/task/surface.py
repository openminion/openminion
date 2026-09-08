from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import quote

from .project.operator import ProjectOperatorResumeAction, ProjectOperatorWorkState
from .runtime.lifecycle import TaskLifecycleState
from .runtime.lifecycle_models import bounded_task_run_error


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
        digest_tasks = _tasks_from_digest_source(
            self.source,
            agent_id=self.agent_id,
            session_id=self.session_id,
            limit=self.limit,
            event_limit=self.event_limit,
        )
        lifecycle_tasks = _tasks_from_lifecycle_source(
            self.source,
            agent_id=self.agent_id,
            limit=self.limit,
        )
        tasks_by_id = {
            str(task.get("id") or ""): task for task in digest_tasks if task.get("id")
        }
        for task in lifecycle_tasks:
            task_id = str(task.get("id") or "")
            prior = tasks_by_id.get(task_id, {})
            merged = {**prior, **task}
            if prior.get("pending_actions"):
                merged["pending_actions"] = prior["pending_actions"]
                merged.update(
                    _operator_projection(
                        str(merged.get("status") or ""),
                        prior["pending_actions"],
                    )
                )
            tasks_by_id[task_id] = merged
        tasks = list(tasks_by_id.values())
        return sorted(
            tasks,
            key=lambda item: (
                _STATUS_ORDER.get(str(item.get("status", "")).upper(), 9),
                str(item.get("id", "")),
            ),
        )[: self.limit]

    def show_task(self, task_id: str) -> dict[str, Any] | None:
        normalized = str(task_id or "").strip()
        if not normalized:
            return None
        for task in self.list_tasks():
            if str(task.get("id", "")) == normalized:
                return task
        if getattr(self.source, "lifecycle_repository", None) is not None:
            return _task_from_lifecycle_record(
                self.source,
                normalized,
                agent_id=self.agent_id,
            )
        return _task_from_digest_record(
            self.source,
            normalized,
            agent_id=self.agent_id,
            session_id=self.session_id,
            event_limit=self.event_limit,
        )

    def list_pending_actions(self) -> list[dict[str, Any]]:
        _, pending_by_id = _pending_actions_index(
            self.source,
            agent_id=self.agent_id,
            session_id=self.session_id,
            event_limit=self.event_limit,
        )
        return sorted(
            pending_by_id.values(),
            key=lambda item: (
                str(item.get("task_id", "")),
                str(item.get("decision_id", "")),
            ),
        )

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
                agent_id=self.agent_id,
                session_id=self.session_id,
            )
        normalized_task_id = str(task_id or "").strip()
        task = self.show_task(normalized_task_id)
        if task is None:
            raise KeyError(f"task not found: {normalized_task_id}")
        if "valid_actions" in task and normalized_action not in task["valid_actions"]:
            state = str(task.get("lifecycle_state") or "unknown")
            raise ValueError(f"cannot {normalized_action} task in {state} state")
        return _apply_lifecycle_action(
            self.source,
            task_id=normalized_task_id,
            action=normalized_action,
            scheduled=_is_scheduled_lifecycle_task(self.source, task),
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
        limit=max(1, min(int(limit), 100)),
        event_limit=max(1, int(event_limit)),
    )


def resolve_task_surface_source(runtime: Any | None) -> Any | None:
    """Resolve the best task owner without creating a parallel task service."""

    direct = _first_task_owner(runtime)
    if direct is not None:
        return direct

    api_runtime = getattr(runtime, "_rt", None)
    if api_runtime is not None and api_runtime is not runtime:
        owner = resolve_task_surface_source(api_runtime)
        if owner is not None:
            return owner

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
    digest = _get_digest(source, agent_id=agent_id, session_id=session_id, limit=limit)
    pending_by_task, _ = _pending_actions_index(
        source,
        agent_id=agent_id,
        session_id=session_id,
        event_limit=event_limit,
    )
    tasks_by_id: dict[str, dict[str, Any]] = {}

    for digest_task in _iter_digest_tasks(digest):
        task_id = str(_value(digest_task, "task_id") or "").strip()
        if not task_id:
            continue
        tasks_by_id[task_id] = _digest_task_payload(
            digest_task,
            pending_actions=list(pending_by_task.get(task_id, [])),
        )

    for task_id, pending_actions in pending_by_task.items():
        tasks_by_id.setdefault(
            task_id,
            {
                "id": task_id,
                "title": f"Task {task_id}",
                "status": "WAITING",
                "steps": [],
                "pending_actions": list(pending_actions),
                **_operator_projection("WAITING", pending_actions),
            },
        )
    return list(tasks_by_id.values())


def _task_from_digest_record(
    source: Any | None,
    task_id: str,
    *,
    agent_id: str,
    session_id: str,
    event_limit: int,
) -> dict[str, Any] | None:
    find_task = getattr(source, "find_task", None)
    if not callable(find_task):
        return None
    task = find_task(task_id)
    if task is None:
        return None
    pending_by_task, _ = _pending_actions_index(
        source,
        agent_id=agent_id,
        session_id=session_id,
        event_limit=event_limit,
    )
    return _digest_task_payload(
        task,
        pending_actions=list(pending_by_task.get(task_id, [])),
    )


def _digest_task_payload(
    task: Any,
    *,
    pending_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    task_id = str(_value(task, "task_id") or "").strip()
    status = _normalize_status(_value(task, "status", "PENDING"))
    next_step_id = str(_value(task, "next_step_id") or "").strip()
    next_step_title = str(_value(task, "next_step_title") or "").strip()
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
        "title": str(_value(task, "title") or task_id),
        "status": status,
        "due_at": _normalize_due(_value(task, "due_at")),
        "steps": steps,
        "pending_actions": pending_actions,
    }
    payload.update(_operator_projection(status, pending_actions))
    project = _project_payload(_value(task, "metadata", {}))
    if project:
        payload["project"] = project
    return payload


def _get_digest(
    source: Any | None, *, agent_id: str, session_id: str, limit: int
) -> Any | None:
    get_digest = getattr(source, "get_digest", None)
    if not callable(get_digest):
        return None
    return get_digest(agent_id=agent_id, session_id=session_id, limit=limit)


def _iter_digest_tasks(digest: Any | None) -> list[Any]:
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
    source: Any | None, *, agent_id: str, limit: int
) -> list[dict[str, Any]]:
    repository = getattr(source, "lifecycle_repository", None)
    list_records = getattr(repository, "list", None)
    if not callable(list_records):
        return []
    records = list_records(limit=limit, agent_id=agent_id)
    return [
        _lifecycle_record_payload(source, record)
        for record in records
        if _agent_matches(record, agent_id)
    ]


def _task_from_lifecycle_record(
    source: Any | None,
    task_id: str,
    *,
    agent_id: str,
) -> dict[str, Any] | None:
    get_task = getattr(source, "get_task", None)
    if not callable(get_task):
        return None
    record = get_task(task_id)
    if record is None:
        return None
    if not _agent_matches(record, agent_id):
        raise PermissionError(f"task is owned by another agent: {task_id}")
    return _lifecycle_record_payload(source, record)


def _agent_matches(record: Any, agent_id: str) -> bool:
    expected = str(agent_id or "").strip()
    owner = str(_value(record, "agent_id") or "").strip()
    return bool(expected) and owner == expected


def _lifecycle_record_payload(source: Any | None, record: Any) -> dict[str, Any]:
    metadata = dict(_value(record, "metadata", {}) or {})
    task_id = str(_value(record, "task_id") or "").strip()
    job = _get_job(source, str(_value(record, "cron_job_id") or task_id).strip())
    title = _task_title(task_id, metadata, job=job)
    scheduled_due = (job or {}).get("next_due_at")
    due_at = (
        str(scheduled_due).strip()
        if scheduled_due
        else _normalize_due(metadata.get("due_at") or metadata.get("wait_at"))
    )
    payload: dict[str, Any] = {
        "id": task_id,
        "title": title,
        "status": _normalize_status(_value(record, "state", "PENDING")),
        "lifecycle_state": str(_value(record, "state", "active")).lower(),
        "due_at": due_at,
        "steps": _progress_steps(metadata),
        "pending_actions": [],
        "agent_id": _value(record, "agent_id"),
        "cron_job_id": _value(record, "cron_job_id"),
        "created_at": _value(record, "created_at"),
        "updated_at": _value(record, "updated_at"),
    }
    payload.update(_operator_projection(str(payload["status"]), ()))
    payload["valid_actions"] = _valid_lifecycle_actions(payload["lifecycle_state"])
    if job is not None:
        schedule = job.get("schedule") or job.get("schedule_json") or {}
        payload["schedule"] = schedule
        payload["schedule_summary"] = _schedule_summary(schedule)
        payload["enabled"] = bool(job.get("enabled", True))
        payload["task_kind"] = _scheduled_task_kind(schedule)
        runs = _list_runs(source, str(payload["cron_job_id"]), limit=5)
        payload["recent_runs"] = runs
        if runs:
            payload["last_run"] = {
                **runs[0],
                "last_error": runs[0].get("error"),
            }
        else:
            payload["last_run"] = metadata.get("last_run")
        payload["daemon_required"] = True
    project = _project_payload(metadata)
    if project:
        payload["project"] = project
    activity = _activity_links(metadata)
    if activity:
        payload["activity"] = activity
    return payload


def _activity_links(metadata: Mapping[str, Any]) -> dict[str, str]:
    session_id = str(
        metadata.get("parent_session_id") or metadata.get("session_id") or ""
    ).strip()
    trace_id = str(metadata.get("trace_id") or "").strip()
    links: dict[str, str] = {}
    if session_id:
        session_segment = quote(session_id, safe="")
        links.update(
            {
                "session_id": session_id,
                "session_events_path": f"/sessions/{session_segment}/events",
                "session_messages_path": f"/sessions/{session_segment}/messages",
                "turn_inputs_path": f"/v1/sessions/{session_segment}/turn-inputs",
            }
        )
    if trace_id:
        links["turn_stream_path"] = f"/v1/turn/{quote(trace_id, safe='')}/stream"
    return links


def _valid_lifecycle_actions(state: Any) -> list[str]:
    normalized = str(state or "").strip().lower()
    if normalized == TaskLifecycleState.ACTIVE.value:
        return ["pause", "cancel"]
    if normalized == TaskLifecycleState.PAUSED.value:
        return ["resume", "cancel"]
    return []


def _scheduled_task_kind(schedule: Any) -> str:
    kind = str((schedule or {}).get("kind") or "").strip()
    return "scheduled_once" if kind == "at" else "scheduled_recurring"


def _schedule_summary(schedule: Any) -> str:
    data = dict(schedule or {})
    kind = str(data.get("kind") or "").strip()
    if kind == "at":
        return f"at:{data.get('at')}"
    if kind == "every":
        return f"every:{data.get('every_ms')}ms"
    if kind == "cron":
        return f"cron:{data.get('expr')} tz={data.get('tz', 'UTC')}"
    return kind


def _list_runs(
    source: Any | None,
    job_id: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    list_runs = getattr(source, "list_scheduled_runs", None)
    if not callable(list_runs):
        return []
    runs = list_runs(job_id=job_id, limit=max(1, min(limit, 20)))
    return [
        {
            "run_id": run.get("run_id"),
            "state": run.get("state"),
            "due_at": run.get("due_at"),
            "available_at": run.get("available_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "summary": str(run.get("summary") or "")[:1000] or None,
            "attempts": int(run.get("attempts", 0) or 0),
            "error": bounded_task_run_error(run.get("error")),
        }
        for run in runs
        if isinstance(run, Mapping)
    ]


def _task_title(
    task_id: str,
    metadata: Mapping[str, Any],
    *,
    job: Mapping[str, Any] | None = None,
) -> str:
    for key in ("title", "name", "goal", "instruction", "summary"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    name = str((job or {}).get("name") or "").strip()
    if name:
        return name
    return f"Task {task_id}"


def _progress_steps(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    progress = metadata.get("progress")
    if not isinstance(progress, Mapping):
        return []
    checkpoint = str(progress.get("last_checkpoint_id") or "").strip()
    if not checkpoint:
        return []
    return [{"order_index": 1, "title": checkpoint, "status": "ACTIVE"}]


def _get_job(source: Any | None, job_id: str) -> dict[str, Any] | None:
    get_job = getattr(source, "get_scheduled_job", None)
    if not callable(get_job) or not job_id:
        return None
    job = get_job(job_id)
    return dict(job) if isinstance(job, Mapping) else None


def _operator_projection(
    status: str,
    pending_actions: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, str]:
    normalized = str(status or "").strip().upper()
    if pending_actions:
        return {
            "operator_state": ProjectOperatorWorkState.WAITING.value,
            "resume_action": ProjectOperatorResumeAction.APPROVE.value,
        }
    if normalized in {"ACTIVE", "PENDING"}:
        return {
            "operator_state": ProjectOperatorWorkState.RUNNING.value,
            "resume_action": ProjectOperatorResumeAction.CONTINUE.value,
        }
    if normalized == "WAITING":
        return {
            "operator_state": ProjectOperatorWorkState.WAITING.value,
            "resume_action": ProjectOperatorResumeAction.CONTINUE.value,
        }
    if normalized == "DONE":
        return {
            "operator_state": ProjectOperatorWorkState.COMPLETED.value,
            "resume_action": ProjectOperatorResumeAction.NONE.value,
        }
    if normalized == "CANCELED":
        return {
            "operator_state": ProjectOperatorWorkState.CANCELLED.value,
            "resume_action": ProjectOperatorResumeAction.NONE.value,
        }
    if normalized == "FAILED":
        return {
            "operator_state": ProjectOperatorWorkState.FAILED.value,
            "resume_action": ProjectOperatorResumeAction.NONE.value,
        }
    return {
        "operator_state": ProjectOperatorWorkState.BLOCKED.value,
        "resume_action": ProjectOperatorResumeAction.INSPECT_BLOCKER.value,
    }


def _pending_actions_index(
    source: Any | None,
    *,
    agent_id: str,
    session_id: str,
    event_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    pending_by_id: dict[str, dict[str, Any]] = {}
    pending_by_task: dict[str, list[dict[str, Any]]] = {}
    if not agent_id or not session_id:
        return pending_by_task, pending_by_id
    list_pending = getattr(source, "list_pending_actions", None)
    if not callable(list_pending):
        return pending_by_task, pending_by_id
    pending_actions = list_pending(
        agent_id=agent_id,
        session_id=session_id,
        limit=event_limit,
    )
    if not isinstance(pending_actions, list):
        raise TypeError("pending actions must be a list")

    for pending in pending_actions:
        policy_request_id = str(_value(pending, "policy_request_id") or "").strip()
        cursor = _value(pending, "cursor")
        task_id = str(_value(cursor, "task_id") or "").strip()
        if not policy_request_id:
            continue
        action = {
            "decision_id": policy_request_id,
            "reason": str(_value(pending, "reason") or "").strip(),
            "tool": "",
            "task_id": task_id,
        }
        pending_by_id[policy_request_id] = action
        if task_id:
            pending_by_task.setdefault(task_id, []).append(action)
    return pending_by_task, pending_by_id


def _resolve_pending_action(
    source: Any | None,
    *,
    outcome: str,
    decision_id: str,
    agent_id: str,
    session_id: str,
) -> dict[str, Any]:
    decision = str(decision_id or "").strip()
    if not decision:
        raise ValueError("decision_id is required for allow/deny")
    if not agent_id or not session_id:
        raise ValueError("agent_id and session_id are required for allow/deny")
    get_pending = getattr(source, "get_pending_action", None)
    if (
        not callable(get_pending)
        or get_pending(
            decision,
            agent_id=agent_id,
            session_id=session_id,
        )
        is None
    ):
        raise ValueError("pending action not found for agent and session")
    resume_pending_action = getattr(source, "resume_pending_action", None)
    if not callable(resume_pending_action):
        raise NotImplementedError("pending action resolution is unavailable")
    resume_pending_action(
        policy_request_id=decision,
        decision_id=f"task-surface:{outcome}:{decision}",
        agent_id=agent_id,
        session_id=session_id,
        trace_id=f"task-surface:{session_id}",
    )
    return {"ok": True, "action": outcome, "decision_id": decision}


def _apply_lifecycle_action(
    source: Any | None,
    *,
    task_id: str,
    action: str,
    scheduled: bool,
) -> dict[str, Any]:
    if not task_id:
        raise ValueError("task_id is required")
    if not scheduled:
        record = _transition_task(source, task_id=task_id, action=action)
        return _task_action_result(source, action=action, record=record)
    method_name = {
        "pause": "pause_task",
        "resume": "resume_task",
        "cancel": "cancel_task",
    }[action]
    method = getattr(source, method_name, None)
    if not callable(method):
        raise NotImplementedError(f"scheduled task {action} is unavailable")
    result = method(task_id)
    record = result[0] if isinstance(result, tuple) else result
    return _task_action_result(source, action=action, record=record)


def _is_scheduled_lifecycle_task(source: Any | None, task: Mapping[str, Any]) -> bool:
    if str(task.get("task_kind") or "").startswith("scheduled_"):
        return True
    if "cron_job_id" not in task:
        return False
    get_task = getattr(source, "get_task", None)
    if not callable(get_task):
        return True
    record = get_task(str(task.get("id") or ""))
    metadata = dict(_value(record, "metadata", {}) or {})
    return not bool(str(metadata.get("kind") or "").strip())


def _transition_task(
    source: Any | None,
    *,
    task_id: str,
    action: str,
) -> Any:
    transition = getattr(source, "transition_task", None)
    if not callable(transition):
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
