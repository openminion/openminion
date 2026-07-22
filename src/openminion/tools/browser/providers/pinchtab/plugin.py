from typing import Any
from collections.abc import Mapping

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.browser import BrowserProviderRegistry, register_provider

from .interfaces import PINCHTAB_PLUGIN_INTERFACE_VERSION
from .provider import PinchTabProvider, PinchTabProviderConfig

provider_id = "pinchtab"


def provider_from_config(
    cfg: Mapping[str, Any] | None = None,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> PinchTabProvider:
    if cfg and isinstance(cfg, Mapping):
        return PinchTabProvider.from_config(cfg, env=env)
    return PinchTabProvider(PinchTabProviderConfig.from_env(env=env), env=env)


def register_browser_provider(
    registry: BrowserProviderRegistry,
    cfg: Mapping[str, Any] | None = None,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> None:
    registry.register(provider_from_config(cfg, env=env))


class PinchTabPlugin:
    tool_id = "browser.pinchtab"
    capabilities = ("browser", "pinchtab", "automation")
    contract_version = PINCHTAB_PLUGIN_INTERFACE_VERSION

    def register(
        self, registry: ToolRegistry | BrowserProviderRegistry | None = None
    ) -> None:
        register(registry)


def register(
    _registry: ToolRegistry | BrowserProviderRegistry | None = None,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> None:
    if isinstance(_registry, BrowserProviderRegistry):
        register_browser_provider(_registry, env=env)
        return

    try:
        register_provider(provider_from_config(env=env))
    except ValueError:
        return


__all__ = [
    "PinchTabPlugin",
    "provider_id",
    "provider_from_config",
    "register",
    "register_browser_provider",
]
