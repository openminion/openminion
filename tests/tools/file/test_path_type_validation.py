"""Non-string `path` values are invalid arguments, not missing files."""

from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.file import REGISTRAR


def _execute(workspace: Path, tool: str, arguments: dict[str, object]):
    registry = ToolRegistry([])
    REGISTRAR.register(registry)
    return execute_tool_spec_call(
        tool=registry.list()[tool],
        arguments=arguments,
        context=ToolExecutionContext(
            channel="console",
            target="tests",
            session_id="path-type",
            metadata={"workspace_root": str(workspace)},
        ),
    )


def test_string_path_is_accepted(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("note body\n", encoding="utf-8")

    result = _execute(tmp_path, "file.read", {"path": "notes.txt"})

    assert result.ok is True
    assert "note body" in result.content


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": 123},
        {"path": 1.5},
        {"path": {"a": 1}},
        {"path": [1, 2]},
    ],
)
def test_non_string_path_is_an_invalid_argument(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    """The validator must reject the value, not hand a stringified path to the tool."""
    result = _execute(tmp_path, "file.read", arguments)

    assert result.ok is False
    assert (result.data or {}).get("error_code") == "invalid_arguments"
    assert "type_mismatch" in (result.error or "")
    assert "does not exist" not in (result.error or "")


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("file.list_dir", {"path": 123}),
        ("file.write", {"path": [1], "content": "x"}),
        ("file.search", {"path": 4.5, "pattern": "x"}),
    ],
)
def test_other_file_tools_reject_non_string_paths(
    tmp_path: Path, tool: str, arguments: dict[str, object]
) -> None:
    result = _execute(tmp_path, tool, arguments)

    assert result.ok is False
    assert (result.data or {}).get("error_code") == "invalid_arguments"


@pytest.mark.parametrize("falsey", [False, 0, [], {}])
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("file.read", {"path": None}),
        ("file.list_dir", {"path": None}),
        ("file.find", {"path": None, "pattern": "*.py"}),
        ("file.search", {"path": None, "pattern": "x"}),
        ("file.write", {"path": None, "content": "x"}),
    ],
)
def test_falsey_non_string_path_is_rejected(
    tmp_path: Path, tool: str, arguments: dict[str, object], falsey: object
) -> None:
    """`False`/`0`/`[]` must not fall back to the current directory."""
    arguments = {**arguments, "path": falsey}

    result = _execute(tmp_path, tool, arguments)

    assert result.ok is False
    assert (result.data or {}).get("error_code") == "invalid_arguments"


def test_omitted_path_on_list_dir_still_defaults_to_the_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / "here.txt").write_text("x", encoding="utf-8")

    result = _execute(tmp_path, "file.list_dir", {})

    assert result.ok is True
    assert "here.txt" in result.content
