from __future__ import annotations

import pytest
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.contracts.model_ids import (
    ALL_MODEL_TOOL_IDS_SET,
    MODEL_TOOL_LIST,
    MODEL_TOOL_SEARCH,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.tool_catalog.plugin import (
    ToolSearchArgs,
    _h_tool_search,
    register,
)


class _FakePolicy:
    def ensure_path_allowed(self, *a, **kw):
        pass

    def limit_int(self, key, default):
        return default


class _FakeCtx:
    policy = _FakePolicy()
    workspace = "."
    run_root = "."
    scope = "READ_ONLY"
    confirm = False
    skill_api = None


_CTX = _FakeCtx()


def test_tool_search_returns_ok():
    result = _h_tool_search({}, _CTX)
    assert result["ok"] is True
    assert "tools" in result
    assert "count" in result


def test_tool_search_returns_model_facing_tools_only():
    result = _h_tool_search({}, _CTX)
    returned_names = {t["name"] for t in result["tools"]}
    # All returned names must be canonical model tool ids
    for name in returned_names:
        assert name in ALL_MODEL_TOOL_IDS_SET, f"non-canonical tool returned: {name}"
    assert MODEL_TOOL_SEARCH not in returned_names
    if MODEL_TOOL_LIST in returned_names:
        # Sanity check when the registry HAS been bootstrapped with
        # the catalog plugin: tool.list is canonical.
        assert MODEL_TOOL_LIST in ALL_MODEL_TOOL_IDS_SET


def test_tool_search_query_filter():
    result = _h_tool_search({"query": "file"}, _CTX)
    assert result["ok"] is True
    for tool in result["tools"]:
        assert (
            "file" in tool["name"].lower()
            or "file" in tool.get("description", "").lower()
        )


def test_tool_search_no_results_for_gibberish():
    result = _h_tool_search({"query": "zzznomatch999"}, _CTX)
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["tools"] == []


def test_tool_search_max_results_respected():
    result = _h_tool_search({"max_results": 2}, _CTX)
    assert result["ok"] is True
    assert len(result["tools"]) <= 2


def test_register_adds_tool_list_and_compat_alias():
    registry = ToolRegistry()
    register(registry)
    tools = registry.list()
    assert MODEL_TOOL_LIST in tools
    assert MODEL_TOOL_SEARCH in tools


def test_registry_execute_calls_supports_tool_list_and_alias() -> None:
    registry = build_default_tool_registry()
    context = ToolExecutionContext(
        channel="console",
        target="pytest",
        session_id="tool-catalog-regression",
        metadata={},
    )

    canonical = registry.execute_calls(
        [ProviderToolCall(name=MODEL_TOOL_LIST, arguments={}, source="test")],
        context=context,
    ).results[0]
    assert canonical.ok is True
    assert canonical.tool_name == MODEL_TOOL_LIST
    assert canonical.data.get("model_tool_name") == MODEL_TOOL_LIST

    alias = registry.execute_calls(
        [ProviderToolCall(name=MODEL_TOOL_SEARCH, arguments={}, source="test")],
        context=context,
    ).results[0]
    assert alias.ok is True
    assert alias.tool_name == MODEL_TOOL_SEARCH
    assert alias.data.get("model_tool_name") == MODEL_TOOL_LIST
    assert alias.data.get("runtime_binding_id") == "runtime.tool.list"


def test_tool_search_args_defaults():
    args = ToolSearchArgs.model_validate({})
    assert args.query == ""
    assert args.max_results == 50


def test_tool_search_args_rejects_extra():
    with pytest.raises(Exception):
        ToolSearchArgs.model_validate({"unknown_field": True})
