"""Direct dispatch refuses a requested declared candidate that has no live spec."""

from __future__ import annotations

import pytest

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.dispatch import resolve_binding_for_call
from openminion.tools.time.constants import TIME_RUNTIME_TOOL_NAMES


@pytest.fixture(scope="module")
def resolution_manager():
    registry = build_default_tool_registry()
    try:
        yield registry._binding_manager()
    finally:
        if registry.mcp_manager is not None:
            registry.mcp_manager.close()


@pytest.mark.parametrize("raw_name", TIME_RUNTIME_TOOL_NAMES)
def test_missing_declared_time_candidate_is_refused(resolution_manager, raw_name):
    available = tuple(name for name in TIME_RUNTIME_TOOL_NAMES if name != raw_name)

    resolution = resolve_binding_for_call(
        raw_tool_name=raw_name,
        available_tool_names=available,
        manager=resolution_manager,
    )

    assert resolution is None


def test_available_declared_time_candidates_use_runtime_direct(resolution_manager):
    for raw_name in TIME_RUNTIME_TOOL_NAMES:
        resolution = resolve_binding_for_call(
            raw_tool_name=raw_name,
            available_tool_names=(raw_name,),
            manager=resolution_manager,
        )
        assert resolution is not None
        assert resolution.source == "runtime_direct"
        assert resolution.model_tool_id == "time"
        assert resolution.runtime_tool_name == raw_name


def test_model_origin_time_dispatch_is_unchanged(resolution_manager):
    canonical = resolve_binding_for_call(
        raw_tool_name="time",
        available_tool_names=("time.now",),
        manager=resolution_manager,
        allow_runtime_direct=False,
    )
    assert canonical is not None
    assert canonical.source == "model_dispatch"
    assert canonical.runtime_tool_name == "time.now"

    fallback = resolve_binding_for_call(
        raw_tool_name="time",
        available_tool_names=("time.in_zone",),
        manager=resolution_manager,
        allow_runtime_direct=False,
    )
    assert fallback is not None
    assert fallback.source == "model_dispatch"
    assert fallback.runtime_tool_name == "time.in_zone"

    assert (
        resolve_binding_for_call(
            raw_tool_name="time.in_zone",
            available_tool_names=("time.in_zone",),
            manager=resolution_manager,
            allow_runtime_direct=False,
        )
        is None
    )


def test_unrelated_family_paths_are_unchanged(resolution_manager):
    direct = resolve_binding_for_call(
        raw_tool_name="fetch.get",
        available_tool_names=("fetch.get",),
        manager=resolution_manager,
    )
    assert direct is not None
    assert direct.source == "runtime_direct"
    assert direct.model_tool_id == "web.fetch"

    canonical = resolve_binding_for_call(
        raw_tool_name="web.fetch",
        available_tool_names=("fetch.head",),
        manager=resolution_manager,
    )
    assert canonical is not None
    assert canonical.source == "model_dispatch"
    assert canonical.runtime_tool_name == "fetch.head"


def test_execute_calls_does_not_fall_back_for_a_missing_declared_candidate():
    registry = build_default_tool_registry()
    try:
        registry.unregister("time.in_zone")
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="time.in_zone",
                    arguments={"timezone": "Europe/Helsinki"},
                    source="inproc",
                )
            ],
            context=ToolExecutionContext(
                channel="test",
                target="test",
                session_id="time-direct",
                metadata={"session_id": "time-direct"},
            ),
        )
    finally:
        if registry.mcp_manager is not None:
            registry.mcp_manager.close()

    result = batch.results[0]
    assert result.ok is False
    assert result.tool_name == "time.in_zone"
    assert result.data["error_code"] == "unknown_tool_name"
    assert "runtime_tool_name" not in result.data
