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


@pytest.mark.parametrize("allow_runtime_direct", [True, False])
def test_canonical_time_never_selects_another_operation(
    resolution_manager, allow_runtime_direct
):
    canonical = resolve_binding_for_call(
        raw_tool_name="time",
        available_tool_names=("time.now", "time.in_zone"),
        manager=resolution_manager,
        allow_runtime_direct=allow_runtime_direct,
    )
    assert canonical is not None
    assert canonical.source == "model_dispatch"
    assert canonical.runtime_tool_name == "time.now"

    unavailable = resolve_binding_for_call(
        raw_tool_name="time",
        available_tool_names=("time.in_zone",),
        manager=resolution_manager,
        allow_runtime_direct=allow_runtime_direct,
    )
    assert unavailable is not None
    assert unavailable.source == "binding_unavailable"
    assert unavailable.runtime_tool_name == ""
    assert unavailable.runtime_fallback_chain == ()


def test_model_origin_explicit_time_names_are_refused(resolution_manager):
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

    missing_direct = resolve_binding_for_call(
        raw_tool_name="fetch.get",
        available_tool_names=("fetch.head",),
        manager=resolution_manager,
    )
    assert missing_direct is not None
    assert missing_direct.source == "model_dispatch"
    assert missing_direct.model_tool_id == "web.fetch"
    assert missing_direct.runtime_tool_name == "fetch.head"


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


def test_execute_calls_refuses_the_canonical_time_substitution():
    registry = build_default_tool_registry()
    try:
        registry.unregister("time.now")
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="time",
                    arguments={"timezone": "UTC"},
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
    assert result.tool_name == "time"
    assert result.data["error_code"] == "unknown_tool_name"
    assert result.data["runtime_resolution_source"] == "binding_unavailable"
    assert result.data["model_tool_name"] == "time"
    assert result.data["runtime_tool_name"] == ""
    assert result.data["runtime_fallback_chain"] == []


def test_unrelated_family_fallback_survives_the_canonical_time_guard():
    registry = build_default_tool_registry()
    try:
        registry.unregister("time.now")
        manager = registry._binding_manager()
        assert registry.model_to_runtime_tool_map()["web.fetch"] == "fetch.get"
        resolution = resolve_binding_for_call(
            raw_tool_name="web.fetch",
            available_tool_names=tuple(registry._tools),
            manager=manager,
            allow_runtime_direct=False,
        )
    finally:
        if registry.mcp_manager is not None:
            registry.mcp_manager.close()

    assert resolution is not None
    assert resolution.source == "model_dispatch"
    assert resolution.runtime_tool_name == "fetch.get"


def test_compiled_time_schema_never_uses_another_operation():
    """The model `time` schema is compiled from the declared `time.now` only."""
    registry = build_default_tool_registry()
    try:
        manager = registry._binding_manager()
        available = set(registry._tools)
        in_zone_schema = manager._runtime_schema_by_tool_name["time.in_zone"]
        # A missing canonical runtime schema must not fall through to a sibling.
        manager._runtime_schema_by_tool_name.pop("time.now")
        manager._compiled = None

        specs = {spec.name: spec for spec in manager.model_provider_specs(available)}
        assert specs["time"].parameters == {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
        assert specs["time"].parameters != in_zone_schema
        assert manager.model_to_runtime_tool_map(available)["time"] == "time.now"
        assert (
            manager.model_runtime_dispatch_map(available)["time"]["runtime_tool_name"]
            == "time.now"
        )

        # With the canonical runtime operation unavailable the model tool has no
        # selection and no model-facing spec at all.
        without_now = available - {"time.now"}
        assert all(
            spec.name != "time" for spec in manager.model_provider_specs(without_now)
        )
        assert "time" not in manager.model_to_runtime_tool_map(without_now)
        unavailable = manager.model_runtime_dispatch_map(without_now)["time"]
        assert unavailable["runtime_tool_name"] == ""
        assert unavailable["runtime_candidates"] == []
    finally:
        if registry.mcp_manager is not None:
            registry.mcp_manager.close()
