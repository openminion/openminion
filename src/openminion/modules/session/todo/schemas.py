from dataclasses import dataclass, field

from openminion.modules.session.todo.constants import TodoStatusValue as TodoItemStatus


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
