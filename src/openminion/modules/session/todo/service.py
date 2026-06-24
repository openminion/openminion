"""In-memory session todo store."""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from openminion.modules.session.todo.constants import (
    DEFAULT_MAX_ITEMS_PER_PLAN,
    DEFAULT_MAX_SESSIONS,
    STATUS_TODO,
    VALID_STATUSES,
)
from openminion.modules.session.todo.errors import (
    InvalidTodoIndexError,
    InvalidTodoStatusError,
    TodoEmptyError,
)
from openminion.modules.session.todo.schemas import Todo, TodoItem, TodoItemStatus


_default_todo_store: "InMemoryTodoStore | None" = None


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


def get_default_todo_store() -> InMemoryTodoStore:
    global _default_todo_store
    if _default_todo_store is None:
        _default_todo_store = InMemoryTodoStore()
    return _default_todo_store


def reset_default_todo_store_for_tests() -> None:
    global _default_todo_store
    _default_todo_store = None


__all__ = (
    "InMemoryTodoStore",
    "get_default_todo_store",
    "reset_default_todo_store_for_tests",
)
