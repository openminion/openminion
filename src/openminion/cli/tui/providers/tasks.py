from __future__ import annotations

from datetime import datetime
from typing import Any

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION


_STATUS_ORDER = {
    "ACTIVE": 0,
    "WAITING": 1,
    "PENDING": 2,
    "DONE": 3,
    "CANCELED": 4,
}


class RuntimeTasksProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(
        self,
        task_ctl: Any | None,
        *,
        agent_id: str,
        session_id: str,
        digest_limit: int = 50,
        event_limit: int = 500,
    ) -> None:
        self._task_ctl = task_ctl
        self._agent_id = str(agent_id or "").strip()
        self._session_id = str(session_id or "").strip()
        self._digest_limit = max(1, int(digest_limit))
        self._event_limit = max(1, int(event_limit))

    def list_tasks(self) -> list[dict[str, Any]]:
        digest = self._safe_get_digest()
        pending_by_task, _ = self._pending_actions_index()

        tasks_by_id: dict[str, dict[str, Any]] = {}
        for digest_task in self._iter_digest_tasks(digest):
            task_id = str(self._value(digest_task, "task_id") or "").strip()
            if not task_id:
                continue
            status = self._normalize_status(
                self._value(digest_task, "status", "PENDING")
            )
            due_at = self._normalize_due(self._value(digest_task, "due_at"))
            next_step_id = str(self._value(digest_task, "next_step_id") or "").strip()
            next_step_title = str(
                self._value(digest_task, "next_step_title") or ""
            ).strip()
            steps: list[dict[str, Any]] = []
            if next_step_id or next_step_title:
                steps.append(
                    {
                        "order_index": 1,
                        "title": next_step_title or next_step_id,
                        "status": "ACTIVE" if status == "ACTIVE" else "PENDING",
                    }
                )

            tasks_by_id[task_id] = {
                "id": task_id,
                "title": str(self._value(digest_task, "title") or task_id),
                "status": status,
                "due_at": due_at,
                "steps": steps,
                "pending_actions": list(pending_by_task.get(task_id, [])),
            }

        for task_id, pending_actions in pending_by_task.items():
            if task_id in tasks_by_id:
                continue
            tasks_by_id[task_id] = {
                "id": task_id,
                "title": f"Task {task_id}",
                "status": "WAITING",
                "steps": [],
                "pending_actions": list(pending_actions),
            }

        return sorted(
            tasks_by_id.values(),
            key=lambda item: (
                _STATUS_ORDER.get(str(item.get("status", "")).upper(), 9),
                str(item.get("id", "")),
            ),
        )

    def list_pending_actions(self) -> list[dict[str, Any]]:
        _, pending_by_id = self._pending_actions_index()
        pending = list(pending_by_id.values())
        pending.sort(
            key=lambda item: (
                str(item.get("task_id", "")),
                str(item.get("decision_id", "")),
            )
        )
        return pending

    def resolve_action(self, decision_id: str, outcome: str) -> bool:
        if self._task_ctl is None:
            return False
        decision = str(decision_id or "").strip()
        normalized_outcome = str(outcome or "").strip().lower()
        if not decision or normalized_outcome not in {"allow", "deny"}:
            return False

        resume_pending_action = getattr(self._task_ctl, "resume_pending_action", None)
        if not callable(resume_pending_action):
            return False

        try:
            resume_pending_action(
                policy_request_id=decision,
                decision_id=f"tui:{normalized_outcome}:{decision}",
                trace_id=f"tui:{self._session_id}",
            )
        except Exception:
            return False
        return True

    def _safe_get_digest(self) -> Any | None:
        if self._task_ctl is None:
            return None
        get_digest = getattr(self._task_ctl, "get_digest", None)
        if not callable(get_digest):
            return None
        try:
            return get_digest(
                agent_id=self._agent_id,
                session_id=self._session_id,
                limit=self._digest_limit,
            )
        except Exception:
            return None

    def _iter_digest_tasks(self, digest: Any | None) -> list[Any]:
        if digest is None:
            return []

        tasks: list[Any] = []
        for attr in ("tasks_active", "tasks_ready"):
            value = self._value(digest, attr, [])
            if isinstance(value, list):
                tasks.extend(value)

        current = self._value(digest, "current_task")
        if current is not None:
            tasks.append(current)

        seen: set[str] = set()
        unique: list[Any] = []
        for task in tasks:
            task_id = str(self._value(task, "task_id") or "").strip()
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            unique.append(task)
        return unique

    def _pending_actions_index(
        self,
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        pending_by_id: dict[str, dict[str, Any]] = {}
        pending_by_task: dict[str, list[dict[str, Any]]] = {}

        if self._task_ctl is None:
            return pending_by_task, pending_by_id

        list_events = getattr(self._task_ctl, "list_events", None)
        if not callable(list_events):
            return pending_by_task, pending_by_id

        try:
            events = list_events()
        except Exception:
            return pending_by_task, pending_by_id
        if not isinstance(events, list):
            return pending_by_task, pending_by_id

        events = events[-self._event_limit :]
        for event in events:
            event_type = str(
                self._value(event, "type") or self._value(event, "event_type") or ""
            ).strip()
            payload = self._value(event, "payload", {})
            if not isinstance(payload, dict):
                payload = {}
            task_id = str(
                self._value(event, "task_id") or payload.get("task_id") or ""
            ).strip()
            policy_request_id = str(payload.get("policy_request_id") or "").strip()
            reason = str(payload.get("reason") or "").strip()
            tool = str(payload.get("tool") or "").strip()

            if event_type == "mission.paused" and policy_request_id:
                action = {
                    "decision_id": policy_request_id,
                    "reason": reason,
                    "tool": tool,
                    "task_id": task_id,
                }
                pending_by_id[policy_request_id] = action
                if task_id:
                    pending_by_task.setdefault(task_id, []).append(action)
                continue

            if event_type == "mission.resumed" and policy_request_id:
                removed = pending_by_id.pop(policy_request_id, None)
                if removed is not None:
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

    @staticmethod
    def _value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _normalize_due(raw_due: Any) -> str | None:
        if raw_due is None:
            return None
        if isinstance(raw_due, datetime):
            return raw_due.date().isoformat()
        text = str(raw_due).strip()
        if not text:
            return None
        try:
            return (
                datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
            )
        except ValueError:
            return text

    @staticmethod
    def _normalize_status(raw_status: Any) -> str:
        value = getattr(raw_status, "value", raw_status)
        text = str(value or "PENDING").strip()
        if "." in text:
            text = text.rsplit(".", 1)[-1]
        text = text.upper()
        return text if text in _STATUS_ORDER else "PENDING"
