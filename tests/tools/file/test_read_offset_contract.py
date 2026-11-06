from __future__ import annotations

from pathlib import Path

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.file import plugin as file_plugin
from openminion.tools.file.constants import (
    FILE_DEFAULT_MAX_READ_CHARS,
    FILE_MAX_READ_CHARS,
)
from openminion.tools.file.plugin import FileReadArgs


def _registry_and_ctx(tmp_path: Path):
    (tmp_path / "big.txt").write_text(
        "".join(f"line {index:04d}\n" for index in range(1, 1201)), encoding="utf-8"
    )
    registry = ToolRegistry()
    file_plugin.register(registry)
    ctx = ToolExecutionContext(
        channel="console",
        target="cli-user",
        metadata={
            "workspace_root": str(tmp_path),
            "tool_workspace_root": str(tmp_path),
        },
    )
    return registry, ctx


def test_truncated_read_reports_next_offset(tmp_path: Path) -> None:
    registry, ctx = _registry_and_ctx(tmp_path)

    result = execute_tool_spec_call(
        tool=registry.get("file.read"),
        arguments={"path": "big.txt", "max_chars": 100, "offset": 20},
        context=ctx,
    )

    assert result.ok
    assert result.content
    assert result.data["truncated"] is True
    assert result.data["next_offset"] == 20 + result.data["returned_length"]
    assert result.data["hint"].startswith("pass next_offset")


def test_continuation_at_next_offset_resumes_the_text(tmp_path: Path) -> None:
    registry, ctx = _registry_and_ctx(tmp_path)
    full = (tmp_path / "big.txt").read_text(encoding="utf-8")

    first = execute_tool_spec_call(
        tool=registry.get("file.read"),
        arguments={"path": "big.txt", "max_chars": 100},
        context=ctx,
    )
    second = execute_tool_spec_call(
        tool=registry.get("file.read"),
        arguments={"path": "big.txt", "offset": first.data["next_offset"]},
        context=ctx,
    )

    returned = first.data["returned_length"]
    assert second.ok
    assert first.content.startswith(full[:20])
    assert returned == 100
    resumed = full[first.data["next_offset"] : first.data["next_offset"] + 10]
    assert second.content.startswith(resumed)


def test_complete_read_has_no_continuation_fields(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("short\n", encoding="utf-8")
    registry = ToolRegistry()
    file_plugin.register(registry)
    ctx = ToolExecutionContext(
        channel="console",
        target="cli-user",
        metadata={
            "workspace_root": str(tmp_path),
            "tool_workspace_root": str(tmp_path),
        },
    )

    result = execute_tool_spec_call(
        tool=registry.get("file.read"),
        arguments={"path": "small.txt"},
        context=ctx,
    )

    assert result.ok
    assert "next_offset" not in result.data
    assert "hint" not in result.data


def test_read_schema_documents_offset_and_limit() -> None:
    offset = FileReadArgs.model_fields["offset"]
    max_chars = FileReadArgs.model_fields["max_chars"]

    assert "character offset" in (offset.description or "").lower()
    assert "next_offset" in (offset.description or "")
    assert str(FILE_DEFAULT_MAX_READ_CHARS) in (max_chars.description or "")
    assert str(FILE_MAX_READ_CHARS) in (max_chars.description or "")
