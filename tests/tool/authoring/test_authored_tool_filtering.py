from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from openminion.modules.tool import ToolRegistry
from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.registry import ToolSpec
from openminion.modules.tool.schema_service import ToolSchemaService


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def test_prompt_schemas_exclude_experimental_authored_tools_by_default(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENMINION_INCLUDE_EXPERIMENTAL_AUTHORED_TOOLS", raising=False)
    registry = ToolRegistry()
    trusted = ToolSpec(
        name="authored.safe@v1",
        args_model=_Args,
        min_scope="POWER_USER",
        handler=lambda args, ctx: {"ok": True},
        tags=("authored", "origin:authored", "trusted"),
        prompt_visible_runtime_name=True,
    )
    trusted.description = "trusted tool"
    registry.add(trusted)
    experimental = ToolSpec(
        name="authored.experimental@v1",
        args_model=_Args,
        min_scope="POWER_USER",
        handler=lambda args, ctx: {"ok": True},
        tags=("authored", "origin:authored", "experimental"),
        prompt_visible_runtime_name=True,
    )
    experimental.description = "experimental tool"
    registry.add(experimental)
    registry.exposure_service.register_profiles(
        (
            ToolExposureProfile(
                profile_id="authored_test",
                title="Authored test",
                summary="Test authored tool exposure.",
                tool_names=frozenset({"authored.safe@v1", "authored.experimental@v1"}),
            ),
        )
    )
    registry.exposure_service.activate("authored_test", session_id="authoring")

    schemas = ToolSchemaService().collect_execution_tool_schemas(
        registry=registry,
        metadata={"session_id": "authoring"},
    )
    names = {item["name"] for item in schemas}
    assert "authored.safe@v1" in names
    assert "authored.experimental@v1" not in names


def test_prompt_schemas_include_experimental_authored_tools_when_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENMINION_INCLUDE_EXPERIMENTAL_AUTHORED_TOOLS", "1")
    registry = ToolRegistry()
    experimental = ToolSpec(
        name="authored.experimental@v1",
        args_model=_Args,
        min_scope="POWER_USER",
        handler=lambda args, ctx: {"ok": True},
        tags=("authored", "origin:authored", "experimental"),
        prompt_visible_runtime_name=True,
    )
    experimental.description = "experimental tool"
    registry.add(experimental)
    registry.exposure_service.register_profiles(
        (
            ToolExposureProfile(
                profile_id="authored_test",
                title="Authored test",
                summary="Test authored tool exposure.",
                tool_names=frozenset({"authored.experimental@v1"}),
            ),
        )
    )
    registry.exposure_service.activate("authored_test", session_id="authoring")

    schemas = ToolSchemaService().collect_execution_tool_schemas(
        registry=registry,
        metadata={"session_id": "authoring"},
    )
    names = {item["name"] for item in schemas}
    assert "authored.experimental@v1" in names
