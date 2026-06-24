class TodoError(ValueError):
    code: str = "PLAN_ERROR"


class TodoEmptyError(TodoError):
    code = "PLAN_EMPTY"


class InvalidTodoIndexError(TodoError):
    code = "INVALID_PLAN_INDEX"


class InvalidTodoStatusError(TodoError):
    code = "INVALID_PLAN_STATUS"


__all__ = (
    "TodoError",
    "TodoEmptyError",
    "InvalidTodoIndexError",
    "InvalidTodoStatusError",
)
