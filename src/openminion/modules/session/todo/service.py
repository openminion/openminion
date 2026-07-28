"""Session todo stores."""

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping, cast

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.config import resolve_module_data_root, resolve_module_home_root
from openminion.modules.session.todo.constants import (
    DEFAULT_MAX_ITEMS_PER_PLAN,
    DEFAULT_MAX_SESSIONS,
    DEFAULT_TODO_STORE_SUBPATH,
    STATUS_TODO,
    VALID_STATUSES,
)
from openminion.modules.session.todo.errors import (
    InvalidTodoIndexError,
    InvalidTodoStatusError,
    TodoEmptyError,
)
from openminion.modules.session.todo.schemas import Todo, TodoItem, TodoItemStatus


_default_todo_store: "InMemoryTodoStore | FileTodoStore | None" = None


class InMemoryTodoStore:
    """Default in-memory todo store."""

    def __init__(
        self,
        *,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_items_per_plan: int = DEFAULT_MAX_ITEMS_PER_PLAN,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_sessions < 1:
            raise ValueError(f"max_sessions must be >= 1, got {max_sessions!r}")
        if max_items_per_plan < 1:
            raise ValueError(
                f"max_items_per_plan must be >= 1, got {max_items_per_plan!r}"
            )
        self._max_sessions = max_sessions
        self._max_items_per_plan = max_items_per_plan
        self._clock = clock
        self._todos: "OrderedDict[str, Todo]" = OrderedDict()
        self._lock = threading.Lock()

    def set_plan(self, session_id: str, items: list[str]) -> Todo:
        if len(items) > self._max_items_per_plan:
            raise InvalidTodoIndexError(
                f"Plan would have {len(items)} items, exceeding cap "
                f"of {self._max_items_per_plan}"
            )
        now = self._clock()
        todo_items = [
            TodoItem(
                index=i,
                text=text,
                status=STATUS_TODO,
                created_at=now,
                updated_at=now,
            )
            for i, text in enumerate(items)
        ]
        todo = Todo(
            session_id=session_id,
            items=todo_items,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._todos[session_id] = todo
            self._todos.move_to_end(session_id)
            self._evict_if_over_cap_locked()
        return todo

    def add_item(
        self,
        session_id: str,
        text: str,
        *,
        position: int = -1,
    ) -> TodoItem:
        now = self._clock()
        with self._lock:
            todo = self._todos.get(session_id)
            if todo is None:
                raise TodoEmptyError(
                    f"No plan set for session {session_id!r}; "
                    f"call set_plan first or use add_item only after set_plan."
                )
            if len(todo.items) >= self._max_items_per_plan:
                raise InvalidTodoIndexError(
                    f"Plan for session {session_id!r} is at the "
                    f"{self._max_items_per_plan}-item cap; cannot add more."
                )
            if position == -1 or position >= len(todo.items):
                insert_at = len(todo.items)
            elif position < -1:
                raise InvalidTodoIndexError(
                    f"position must be -1 (append) or a non-negative "
                    f"integer, got {position!r}"
                )
            else:
                insert_at = position

            new_item = TodoItem(
                index=insert_at,
                text=text,
                status=STATUS_TODO,
                created_at=now,
                updated_at=now,
            )
            todo.items.insert(insert_at, new_item)
            for offset, item in enumerate(todo.items):
                item.index = offset
            todo.updated_at = now
            self._todos.move_to_end(session_id)
            return new_item

    def update_item_status(
        self,
        session_id: str,
        index: int,
        status: TodoItemStatus,
    ) -> TodoItem:
        if status not in VALID_STATUSES:
            raise InvalidTodoStatusError(
                f"status must be one of {VALID_STATUSES}, got {status!r}"
            )
        now = self._clock()
        with self._lock:
            todo = self._todos.get(session_id)
            if todo is None:
                raise TodoEmptyError(f"No plan set for session {session_id!r}")
            if index < 0 or index >= len(todo.items):
                raise InvalidTodoIndexError(
                    f"index {index} out of range for plan with "
                    f"{len(todo.items)} item(s)"
                )
            item = todo.items[index]
            item.status = status
            item.updated_at = now
            todo.updated_at = now
            self._todos.move_to_end(session_id)
            return item

    def get_plan(self, session_id: str) -> Todo | None:
        with self._lock:
            todo = self._todos.get(session_id)
            if todo is not None:
                self._todos.move_to_end(session_id)
            return todo

    def clear_plan(self, session_id: str) -> None:
        with self._lock:
            self._todos.pop(session_id, None)

    evict = clear_plan

    def session_count(self) -> int:
        with self._lock:
            return len(self._todos)

    def _evict_if_over_cap_locked(self) -> None:
        while len(self._todos) > self._max_sessions:
            self._todos.popitem(last=False)


class FileTodoStore(InMemoryTodoStore):
    """JSON-backed todo store for resumed sessions."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        max_items_per_plan: int = DEFAULT_MAX_ITEMS_PER_PLAN,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path).expanduser().resolve(strict=False)
        super().__init__(
            max_sessions=max_sessions,
            max_items_per_plan=max_items_per_plan,
            clock=clock,
        )
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def set_plan(self, session_id: str, items: list[str]) -> Todo:
        todo = super().set_plan(session_id, items)
        self._save()
        return todo

    def add_item(
        self,
        session_id: str,
        text: str,
        *,
        position: int = -1,
    ) -> TodoItem:
        item = super().add_item(session_id, text, position=position)
        self._save()
        return item

    def update_item_status(
        self,
        session_id: str,
        index: int,
        status: TodoItemStatus,
    ) -> TodoItem:
        item = super().update_item_status(session_id, index, status)
        self._save()
        return item

    def clear_plan(self, session_id: str) -> None:
        super().clear_plan(session_id)
        self._save()

    evict = clear_plan

    def _load(self) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"Todo store payload must be a JSON object: {self._path}")
        sessions = payload.get("sessions", {})
        if not isinstance(sessions, Mapping):
            raise ValueError(f"Todo store sessions must be a JSON object: {self._path}")
        with self._lock:
            self._todos.clear()
            for session_id, raw_todo in sessions.items():
                if not isinstance(raw_todo, Mapping):
                    continue
                todo = _todo_from_payload(str(session_id), raw_todo)
                self._todos[todo.session_id] = todo
            self._evict_if_over_cap_locked()

    def _save(self) -> None:
        with self._lock:
            payload = {
                "version": 1,
                "sessions": {
                    session_id: _todo_to_payload(todo)
                    for session_id, todo in self._todos.items()
                },
            }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        tmp_path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp_path.replace(self._path)


def resolve_default_todo_store_path(
    *,
    home_root: Path | None = None,
    data_root: Path | str | None = None,
    env: EnvironmentConfig | Mapping[str, str] | None = None,
) -> Path:
    env_source = env if env is not None else resolve_environment_config()
    resolved_home = resolve_module_home_root(
        home_root,
        env_source,
        fallback_to_cwd=True,
    )
    resolved_data = resolve_module_data_root(
        home_root=resolved_home,
        env=env_source,
        data_root=data_root,
    )
    base = resolved_data or resolved_home or Path.cwd().resolve(strict=False)
    return (base / DEFAULT_TODO_STORE_SUBPATH).resolve(strict=False)


def get_default_todo_store() -> InMemoryTodoStore | FileTodoStore:
    global _default_todo_store
    if _default_todo_store is None:
        _default_todo_store = FileTodoStore(resolve_default_todo_store_path())
    return _default_todo_store


def reset_default_todo_store_for_tests() -> None:
    global _default_todo_store
    _default_todo_store = InMemoryTodoStore()


def _todo_to_payload(todo: Todo) -> dict[str, Any]:
    return {
        "session_id": todo.session_id,
        "created_at": todo.created_at,
        "updated_at": todo.updated_at,
        "items": [
            {
                "index": item.index,
                "text": item.text,
                "status": item.status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in todo.items
        ],
    }


def _todo_from_payload(session_id: str, payload: Mapping[str, Any]) -> Todo:
    items: list[TodoItem] = []
    for offset, raw_item in enumerate(payload.get("items", []) or []):
        if not isinstance(raw_item, Mapping):
            continue
        status = _coerce_status(raw_item.get("status", STATUS_TODO))
        items.append(
            TodoItem(
                index=offset,
                text=str(raw_item.get("text", "") or ""),
                status=status,
                created_at=float(raw_item.get("created_at", 0.0) or 0.0),
                updated_at=float(raw_item.get("updated_at", 0.0) or 0.0),
            )
        )
    return Todo(
        session_id=str(payload.get("session_id", session_id) or session_id),
        items=items,
        created_at=float(payload.get("created_at", 0.0) or 0.0),
        updated_at=float(payload.get("updated_at", 0.0) or 0.0),
    )


def _coerce_status(value: Any) -> TodoItemStatus:
    status = str(value or STATUS_TODO)
    if status not in VALID_STATUSES:
        return STATUS_TODO
    return cast(TodoItemStatus, status)


__all__ = (
    "FileTodoStore",
    "InMemoryTodoStore",
    "get_default_todo_store",
    "resolve_default_todo_store_path",
    "reset_default_todo_store_for_tests",
)
