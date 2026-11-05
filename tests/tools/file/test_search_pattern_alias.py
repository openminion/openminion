from __future__ import annotations

from pathlib import Path

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.file import plugin as file_plugin
from openminion.tools.file.plugin import FileSearchArgs


def _registry_and_ctx(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
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


def test_search_accepts_pattern_alias(tmp_path: Path) -> None:
    registry, ctx = _registry_and_ctx(tmp_path)

    by_query = execute_tool_spec_call(
        tool=registry.get("file.search"), arguments={"query": "beta"}, context=ctx
    )
    by_pattern = execute_tool_spec_call(
        tool=registry.get("file.search"), arguments={"pattern": "beta"}, context=ctx
    )

    assert by_query.ok and by_pattern.ok
    assert by_pattern.data["matches"] == by_query.data["matches"]
    assert by_pattern.data["matches"], "alias must find the same matches"


def test_search_schema_documents_the_alias() -> None:
    description = FileSearchArgs.model_fields["query"].description or ""

    assert "pattern" in description
    assert "not a glob" in description


def test_search_still_rejects_unknown_keys(tmp_path: Path) -> None:
    registry, ctx = _registry_and_ctx(tmp_path)

    result = execute_tool_spec_call(
        tool=registry.get("file.search"), arguments={"grep": "beta"}, context=ctx
    )

    assert not result.ok
    assert "query" in (result.error or "")
    extra = {
        item["field_path"]
        for item in result.data["tcrp.validation_errors"]
        if item["error_code"] == "extra_forbidden"
    }
    assert extra == {"grep"}


def test_find_still_requires_its_glob_pattern(tmp_path: Path) -> None:
    registry, ctx = _registry_and_ctx(tmp_path)

    result = execute_tool_spec_call(
        tool=registry.get("file.find"), arguments={"query": "*.txt"}, context=ctx
    )

    assert not result.ok
    assert "extra_forbidden" in (result.error or "")
