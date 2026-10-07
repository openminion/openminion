"""Commerce tool registrar."""

from dataclasses import dataclass, replace
from typing import Any

from openminion.modules.tool.framework import (
    ToolFamilySpec,
    derive_manifest,
    derive_tool_specs,
)
from openminion.tools.commerce.config import coerce_commerce_tool_runtime_config
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool import ToolRegisterContext

from .family import COMMERCE_FAMILY
from .interfaces import ALL_COMMERCE_TOOLS


def optional_contract_omissions(config: Any) -> tuple[set[str], set[str]]:
    context = ToolRegisterContext(module_id="commerce", config=config)
    settings = _commerce_config(context)
    active = (
        {tool.name for tool in _active_family(context).tools}
        if settings and settings.enabled
        else set()
    )
    omitted = set(ALL_COMMERCE_TOOLS) - active
    return omitted, {f"runtime.{name}" for name in omitted}


def _commerce_config(ctx: ToolRegisterContext | None) -> Any:
    if ctx is None:
        return None
    runtime_config = getattr(ctx.config, "runtime", ctx.config)
    tools_config = getattr(runtime_config, "tools", None)
    commerce_config = coerce_commerce_tool_runtime_config(
        getattr(tools_config, "commerce", None)
    )
    return commerce_config


def _active_family(ctx: ToolRegisterContext | None) -> ToolFamilySpec:
    config = _commerce_config(ctx)
    if ctx is None or config and config.order_actions_enabled:
        count = 5
    elif config and config.writes_enabled:
        count = 3
    else:
        count = 2
    tools = COMMERCE_FAMILY.tools[:count]
    profiles = tuple(
        replace(
            profile,
            tool_names=frozenset(tool.name for tool in tools),
        )
        for profile in COMMERCE_FAMILY.exposure_profiles
    )
    return replace(COMMERCE_FAMILY, tools=tools, exposure_profiles=profiles)


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
