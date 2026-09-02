from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.brain.tool_catalog.runtime import RunnerToolCatalog
from openminion.modules.brain.tools.schema import collect_runtime_tool_names
from openminion.modules.tool import ToolRegistry, ToolSpec


def _adapter(workspace_root: Path) -> ToolAdapter:
    registry = ToolRegistry()
    for name in ("safe.read", "danger.write"):
        registry.add(
            ToolSpec(
                name=name,
                args_model=dict,
                min_scope="READ_ONLY",
                handler=lambda _arguments, _ctx, tool_name=name: {
                    "ok": True,
                    "content": tool_name,
                },
            )
        )
    return ToolAdapter(workspace_root=workspace_root, runtime_registry=registry)


def test_turn_tool_allowlist_filters_catalog_and_execution(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    runner = SimpleNamespace(tool_api=adapter)

    with adapter.restrict_tools(("safe.read",)):
        assert collect_runtime_tool_names(runner) == frozenset({"safe.read"})
        assert RunnerToolCatalog(runner).list_tool_names() == {"safe.read"}
        assert RunnerToolCatalog(runner).get_tool_schema("danger.write") is None
        result = adapter.execute(
            command={"tool_name": "danger.write", "args": {}},
            session_id="child-session",
            trace_id="child-turn",
        )

    assert result["status"] == "error"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert RunnerToolCatalog(runner).list_tool_names() == {
        "safe.read",
        "danger.write",
    }


def test_turn_tool_allowlist_is_isolated_between_concurrent_children(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path)
    barrier = Barrier(2)

    def visible_tools(tool_name: str) -> frozenset[str]:
        with adapter.restrict_tools((tool_name,)):
            barrier.wait()
            runner = SimpleNamespace(tool_api=adapter)
            return collect_runtime_tool_names(runner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = {
            future.result()
            for future in (
                executor.submit(visible_tools, "safe.read"),
                executor.submit(visible_tools, "danger.write"),
            )
        }

    assert results == {
        frozenset({"safe.read"}),
        frozenset({"danger.write"}),
    }
