from dataclasses import dataclass, field
from typing import Literal

TodoItemStatus = Literal["todo", "in_progress", "done", "blocked"]


@dataclass
class TodoItem:
    index: int
    text: str
    status: TodoItemStatus = "todo"
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class Todo:
    """Session-scoped todo collection."""

    session_id: str
    items: list[TodoItem] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def summary(self) -> str:
        total = len(self.items)
        done = sum(1 for item in self.items if item.status == "done")
        in_progress = sum(1 for item in self.items if item.status == "in_progress")
        return f"{done}/{total} done, {in_progress} in progress"


__all__ = ("Todo", "TodoItem", "TodoItemStatus")
