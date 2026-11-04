from __future__ import annotations

import pytest

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.framework import ToolFamilySpec, derive_tool_specs
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools import tool


def _registry(*decls) -> ToolRegistry:
    registry = ToolRegistry()
    for spec in derive_tool_specs(ToolFamilySpec(module_id="verify", tools=decls)):
        registry.add(spec)
    return registry


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext(channel="console", target="cli-user", metadata={})


def _call(registry: ToolRegistry, name: str, arguments: dict):
    return execute_tool_spec_call(
        tool=registry.get(name), arguments=arguments, context=_ctx()
    )


@tool(name="verify.add_sync_posonly")
def add_sync(a: int, b: int, /) -> int:
    return a + b


@tool(name="verify.add_async_posonly", idempotent=True)
async def add_async(a: int, b: int, /) -> int:
    return a + b


@tool(name="verify.async_runtime_error")
async def async_runtime_error() -> int:
    raise ToolRuntimeError("POLICY_DENIED", "async handler refused")


@tool(name="verify.async_value_error")
async def async_value_error() -> int:
    raise ValueError("async handler exploded")


def test_async_handler_result_is_awaited() -> None:
    registry = _registry(add_async.tool_decl)

    result = _call(registry, "verify.add_async_posonly", {"a": 2, "b": 3})

    assert result.ok is True
    assert result.data["result"] == 5
    assert "coroutine object" not in str(result.content)


def test_sync_handler_is_unaffected() -> None:
    registry = _registry(add_sync.tool_decl)

    result = _call(registry, "verify.add_sync_posonly", {"a": 2, "b": 3})

    assert result.ok is True
    assert result.data["result"] == 5


def test_async_handler_tool_error_is_structured() -> None:
    registry = _registry(async_runtime_error.tool_decl)

    result = _call(registry, "verify.async_runtime_error", {})

    assert result.ok is False
    assert result.data.get("error_code") == "POLICY_DENIED"
    assert "async handler refused" in (result.error or "")


def test_async_handler_exception_is_structured() -> None:
    registry = _registry(async_value_error.tool_decl)

    result = _call(registry, "verify.async_value_error", {})

    assert result.ok is False
    assert "ValueError" in (result.error or "")


@pytest.mark.asyncio
async def test_async_handler_executes_inside_a_running_loop() -> None:
    registry = _registry(add_async.tool_decl)

    result = _call(registry, "verify.add_async_posonly", {"a": 4, "b": 5})

    assert result.ok is True
    assert result.data["result"] == 9
