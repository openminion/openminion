"""Commerce tool registrar."""

from dataclasses import dataclass, replace
from typing import Any

from openminion.modules.tool.framework import (
    ToolFamilySpec,
    derive_manifest,
    derive_tool_specs,
)
from openminion.modules.commerce.config import coerce_commerce_tool_runtime_config
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool import ToolRegisterContext

from .family import COMMERCE_FAMILY


def _writes_enabled(ctx: ToolRegisterContext | None) -> bool:
    if ctx is None:
        return True
    runtime_config = getattr(ctx.config, "runtime", ctx.config)
    tools_config = getattr(runtime_config, "tools", None)
    commerce_config = coerce_commerce_tool_runtime_config(
        getattr(tools_config, "commerce", None)
    )
    return bool(commerce_config and commerce_config.writes_enabled)


def _active_family(ctx: ToolRegisterContext | None) -> ToolFamilySpec:
    if _writes_enabled(ctx):
        return COMMERCE_FAMILY
    return replace(COMMERCE_FAMILY, tools=COMMERCE_FAMILY.tools[:2])


@dataclass(frozen=True)
class CommerceRegistrar:
    module_id: str = "commerce"
    is_provider_only: bool = False

    def get_manifest(self, ctx: ToolRegisterContext | None) -> Any:
        return derive_manifest(_active_family(ctx))

    def register(
        self, registry: ToolRegistry, ctx: ToolRegisterContext | None = None
    ) -> None:
        family = _active_family(ctx)
        for profile in family.exposure_profiles:
            registry.exposure_service.register_profiles((profile,))
        for spec in derive_tool_specs(family):
            registry.add(spec)


REGISTRAR = CommerceRegistrar()

__all__ = ["CommerceRegistrar", "REGISTRAR"]
