from pathlib import Path
from typing import Literal

TodoStatusValue = Literal["todo", "in_progress", "done", "blocked"]

STATUS_TODO: TodoStatusValue = "todo"
STATUS_IN_PROGRESS: TodoStatusValue = "in_progress"
STATUS_DONE: TodoStatusValue = "done"
STATUS_BLOCKED: TodoStatusValue = "blocked"

VALID_STATUSES: tuple[TodoStatusValue, ...] = (
    STATUS_TODO,
    STATUS_IN_PROGRESS,
    STATUS_DONE,
    STATUS_BLOCKED,
)

DEFAULT_MAX_SESSIONS = 100
DEFAULT_MAX_ITEMS_PER_PLAN = 100
DEFAULT_TODO_STORE_SUBPATH = Path("session") / "todo.json"
