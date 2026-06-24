from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.session.todo import (
    InMemoryTodoStore,
    Todo,
    TodoError,
    TodoItem,
    get_default_todo_store,
    reset_default_todo_store_for_tests,
)
from openminion.modules.session.todo.constants import VALID_STATUSES
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime import RuntimeContext


_plan_store: InMemoryTodoStore | None = None


def _get_plan_store() -> InMemoryTodoStore:
    return _plan_store if _plan_store is not None else get_default_todo_store()


def _reset_store_for_tests() -> None:
    global _plan_store
    _plan_store = None
    reset_default_todo_store_for_tests()


_FALLBACK_SESSION_ID = "_default"


def _resolve_session_id(ctx: RuntimeContext) -> str:
    raw = str(getattr(ctx, "session_id", "") or "").strip()
    return raw or _FALLBACK_SESSION_ID


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanSetArgs(_StrictModel):
    items: list[str] = Field(
        ...,
        description=(
            "Plan items as a list of short, action-oriented strings. "
            "Replaces any existing plan for this session."
        ),
        min_length=0,
    )


class PlanAddArgs(_StrictModel):
    item: str = Field(
        ..., min_length=1, description="The new item to append or insert."
    )
    position: int = Field(
        default=-1,
        description=(
            "Insert position. -1 (default) appends; non-negative integers "
            "insert at that index, shifting later items down."
        ),
    )


class PlanUpdateArgs(_StrictModel):
    index: int = Field(..., ge=0, description="Zero-based item index.")
    status: str = Field(
        ...,
        description=(f"One of: {', '.join(VALID_STATUSES)}."),
    )


class PlanCompleteArgs(_StrictModel):
    index: int = Field(..., ge=0, description="Zero-based item index to mark done.")


class PlanListArgs(_StrictModel):
    pass


class PlanClearArgs(_StrictModel):
    pass


class TodoWriteItem(_StrictModel):
    text: str = Field(..., min_length=1)
    status: str = Field(
        default="todo",
        description=(f"One of: {', '.join(VALID_STATUSES)}."),
    )


class TodoWriteArgs(_StrictModel):
    todos: list[TodoWriteItem] = Field(
        ...,
        description="Full session todo list to replace the current checklist.",
        min_length=0,
    )


def _serialize_item(item: TodoItem) -> dict[str, Any]:
    return {
        "index": item.index,
        "text": item.text,
        "status": item.status,
    }


def _serialize_plan(todo: Todo | None, *, session_id: str) -> dict[str, Any]:
    if todo is None:
        return {
            "session_id": session_id,
            "items": [],
            "summary": "0/0 done, 0 in progress",
        }
    return {
        "session_id": todo.session_id,
        "items": [_serialize_item(item) for item in todo.items],
        "summary": todo.summary(),
    }


def _ok_payload(
    todo: Todo | None,
    *,
    session_id: str,
    message: str,
) -> dict[str, Any]:
    return {
        "plan": _serialize_plan(todo, session_id=session_id),
        "message": message,
    }


def _raise_as_tool_error(exc: TodoError, *, session_id: str) -> None:
    raise ToolRuntimeError(
        exc.code,
        str(exc),
        {"session_id": session_id},
    ) from exc


def _mutate_and_get_plan(
    session_id: str,
    mutate: Callable[[InMemoryTodoStore], None],
) -> Todo:
    try:
        mutate(store := _get_plan_store())
        if (todo := store.get_plan(session_id)) is not None:
            return todo
    except TodoError as exc:
        _raise_as_tool_error(exc, session_id=session_id)
    raise ToolRuntimeError(
        "PLAN_EMPTY",
        "No plan set for this session.",
        {"session_id": session_id},
    )


def _h_set(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    session_id = _resolve_session_id(ctx)
    items = list(args.get("items", []) or [])
    try:
        todo = _get_plan_store().set_plan(session_id, items)
    except TodoError as exc:
        _raise_as_tool_error(exc, session_id=session_id)
    return _ok_payload(
        todo,
        session_id=session_id,
        message=f"Plan set with {len(todo.items)} item(s).",
    )


def _h_add(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    session_id = _resolve_session_id(ctx)
    text = str(args.get("item") or "").strip()
    position = int(args.get("position", -1))
    todo = _mutate_and_get_plan(
        session_id,
        lambda store: store.add_item(session_id, text, position=position),
    )
    return _ok_payload(
        todo,
        session_id=session_id,
        message=f"Item added; plan has {len(todo.items)} item(s).",
    )


def _h_update(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    session_id = _resolve_session_id(ctx)
    index = int(args.get("index", -1))
    status = str(args.get("status") or "")
    todo = _mutate_and_get_plan(
        session_id,
        lambda store: store.update_item_status(session_id, index, status),  # type: ignore[arg-type]
    )
    return _ok_payload(
        todo,
        session_id=session_id,
        message=f"Item {index} -> {status}.",
    )


def _h_complete(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    session_id = _resolve_session_id(ctx)
    index = int(args.get("index", -1))
    todo = _mutate_and_get_plan(
        session_id,
        lambda store: store.update_item_status(session_id, index, "done"),
    )
    return _ok_payload(
        todo,
        session_id=session_id,
        message=f"Item {index} marked done.",
    )


def _h_list(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    session_id = _resolve_session_id(ctx)
    todo = _get_plan_store().get_plan(session_id)
    item_count = len(todo.items) if todo is not None else 0
    return _ok_payload(
        todo,
        session_id=session_id,
        message=(
            f"Plan has {item_count} item(s)."
            if todo is not None
            else "No plan set for this session."
        ),
    )


def _h_clear(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    session_id = _resolve_session_id(ctx)
    _get_plan_store().clear_plan(session_id)
    return _ok_payload(
        None,
        session_id=session_id,
        message="Plan cleared.",
    )


def _h_todo_write(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    session_id = _resolve_session_id(ctx)
    raw_todos = list(args.get("todos", []) or [])
    items = [
        item.model_dump() if isinstance(item, TodoWriteItem) else dict(item)
        for item in raw_todos
    ]

    def _write_items(store: InMemoryTodoStore) -> None:
        store.set_plan(
            session_id,
            [str(item.get("text", "") or "").strip() for item in items],
        )
        for index, item in enumerate(items):
            status = str(item.get("status", "todo") or "todo")
            if status != "todo":
                store.update_item_status(session_id, index, status)  # type: ignore[arg-type]

    todo = _mutate_and_get_plan(session_id, _write_items)
    return _ok_payload(
        todo,
        session_id=session_id,
        message=f"Todo list updated with {len(todo.items)} item(s).",
    )
