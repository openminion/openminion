"""Declarative framework for tool-family registration surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from openminion.modules.tool.contracts.manifest import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.registrar import (
    ToolModuleRegistrar,
    ToolRegisterContext,
)

if TYPE_CHECKING:
    from openminion.modules.tool.exposure import ToolExposureProfile


Handler = Callable[[dict[str, Any], Any], Any]


@dataclass(frozen=True)
class ToolDecl:
    """A single tool declaration inside a `ToolFamilySpec`."""

    name: str
    args_model: type[BaseModel]
    handler: Handler
    description: str = ""
    min_scope: str | None = None
    dangerous: bool = False
    idempotent: bool = False
    tags: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    block_under_readonly: bool = False


@dataclass(frozen=True)
class ToolFamilySpec:
    """Declarative spec for one `tools/<family>/` module."""

    module_id: str
    tools: tuple[ToolDecl, ...] = ()
    min_scope_default: str = "WRITE_SAFE"
    common_tags: tuple[str, ...] = ()
    common_capabilities: tuple[str, ...] = ()
    is_provider_only: bool = False
    provider_registration: Callable[[], None] | None = None
    exposure_profiles: tuple[ToolExposureProfile, ...] = ()

    def __post_init__(self) -> None:
        tool_names = {tool.name for tool in self.tools}
        external_names = sorted(
            {
                name
                for profile in self.exposure_profiles
                for name in profile.tool_names
                if name not in tool_names
            }
        )
        if external_names:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "tool-family exposure profiles can only reference family tools",
                {"module_id": self.module_id, "tool_names": external_names},
            )


def derive_model_tool_id(tool_name: str) -> str:
    """Derive the model-tool id from the tool name."""

    return tool_name


def derive_runtime_binding_id(tool_name: str) -> str:
    """Derive the runtime-binding id from the tool name."""

    return f"runtime.{tool_name}"


def derive_tool_specs(family: ToolFamilySpec) -> list[ToolSpec]:
    """Derive the `ToolSpec` list that the family's `register()` would add
    to the `ToolRegistry`.

    Resolves per-tool overrides over family defaults for `min_scope`,
    `tags`, and `capabilities`.
    """

    specs: list[ToolSpec] = []
    for tool in family.tools:
        scope = tool.min_scope or family.min_scope_default
        merged_tags = family.common_tags + tool.tags
        merged_caps = family.common_capabilities + tool.capabilities
        specs.append(
            ToolSpec(
                name=tool.name,
                args_model=tool.args_model,
                min_scope=scope,
                handler=tool.handler,
                dangerous=tool.dangerous,
                idempotent=tool.idempotent,
                tags=merged_tags,
                capabilities=merged_caps,
                block_under_readonly=tool.block_under_readonly,
            )
        )
    return specs


def derive_manifest(family: ToolFamilySpec) -> ToolBindingManifest:
    """Build the manifest for one tool family."""

    model_tools = tuple(
        ModelToolDef(
            model_tool_id=derive_model_tool_id(tool.name),
            description=tool.description,
            parameters={},
            aliases=tool.aliases,
        )
        for tool in family.tools
    )
    runtime_bindings = tuple(
        RuntimeBindingDef(
            runtime_binding_id=derive_runtime_binding_id(tool.name),
            model_tool_id=derive_model_tool_id(tool.name),
            runtime_candidates=(tool.name,),
        )
        for tool in family.tools
    )
    return ToolBindingManifest(
        module_id=family.module_id,
        model_tools=model_tools,
        runtime_bindings=runtime_bindings,
    )


@dataclass
class GeneratedRegistrar:
    """`ToolModuleRegistrar` adapter for a `ToolFamilySpec`."""

    module_id: str
    is_provider_only: bool
    _family: ToolFamilySpec = field(repr=False)

    def register(
        self,
        registry: ToolRegistry,
        ctx: ToolRegisterContext | None = None,
    ) -> None:
        del ctx
        registry.exposure_service.register_profiles(self._family.exposure_profiles)
        for spec in derive_tool_specs(self._family):
            registry.add(spec)
        if self._family.provider_registration is not None:
            self._family.provider_registration()

    def get_manifest(self, ctx: ToolRegisterContext | None) -> Any | None:
        del ctx
        return derive_manifest(self._family)


def build_registrar(family: ToolFamilySpec) -> ToolModuleRegistrar:
    """Build a `ToolModuleRegistrar` adapter from a `ToolFamilySpec`.

    The returned object conforms to the `ToolModuleRegistrar` protocol and
    is what a family's `__init__.py` should re-export as `REGISTRAR` so
    `bootstrap/registration.py` picks it up unchanged.
    """

    return GeneratedRegistrar(
        module_id=family.module_id,
        is_provider_only=family.is_provider_only,
        _family=family,
    )


__all__ = [
    "GeneratedRegistrar",
    "Handler",
    "ToolDecl",
    "ToolFamilySpec",
    "build_registrar",
    "derive_manifest",
    "derive_model_tool_id",
    "derive_runtime_binding_id",
    "derive_tool_specs",
]
