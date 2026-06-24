from typing import Protocol

from openminion.modules.session.todo.schemas import Todo, TodoItem, TodoItemStatus


class TodoStore(Protocol):
    """Storage owner for session-scoped todos."""

    def set_plan(self, session_id: str, items: list[str]) -> Todo: ...

    def add_item(
        self,
        session_id: str,
        text: str,
        *,
        position: int = -1,
    ) -> TodoItem: ...

    def update_item_status(
        self,
        session_id: str,
        index: int,
        status: TodoItemStatus,
    ) -> TodoItem: ...

    def get_plan(self, session_id: str) -> Todo | None: ...

    def clear_plan(self, session_id: str) -> None: ...

    def evict(self, session_id: str) -> None: ...


__all__ = ("TodoStore",)
