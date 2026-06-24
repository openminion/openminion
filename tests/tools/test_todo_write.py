from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.contracts.display_names import display_name_for_tool_name
from openminion.modules.tool.contracts.model_ids import MODEL_TODO_WRITE
from openminion.tools.todo.plugin import _reset_store_for_tests


def setup_function() -> None:
    _reset_store_for_tests()


def test_todo_write_registered_in_default_registry() -> None:
    registry = build_default_tool_registry()

    assert MODEL_TODO_WRITE in registry.list()
    assert display_name_for_tool_name(MODEL_TODO_WRITE) == "Write Todos"


def test_todo_write_replaces_session_checklist() -> None:
    registry = build_default_tool_registry()
    spec = registry.get(MODEL_TODO_WRITE)

    result = spec.handler(
        {
            "todos": [
                {"text": "Map current state", "status": "done"},
                {"text": "Ship renderer", "status": "in_progress"},
                {"text": "Close tracker", "status": "todo"},
            ]
        },
        SimpleNamespace(session_id="session-1"),
    )

    assert result["message"] == "Todo list updated with 3 item(s)."
    assert result["plan"]["summary"] == "1/3 done, 1 in progress"
    assert result["plan"]["items"] == [
        {"index": 0, "text": "Map current state", "status": "done"},
        {"index": 1, "text": "Ship renderer", "status": "in_progress"},
        {"index": 2, "text": "Close tracker", "status": "todo"},
    ]
