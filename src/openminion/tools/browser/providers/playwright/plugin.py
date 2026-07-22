from typing import Any
from collections.abc import Mapping

from openminion.base.config.env import EnvironmentConfig

from openminion.tools.browser import BrowserProviderRegistry, register_provider

from .config import provider_config_from_mapping
from .provider import PlaywrightProvider


def provider_from_config(
    cfg: Mapping[str, Any] | None = None,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> PlaywrightProvider:
    return PlaywrightProvider(provider_config_from_mapping(cfg, env=env), env=env)


def register_browser_provider(
    registry: BrowserProviderRegistry,
    cfg: Mapping[str, Any] | None = None,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> None:
    """Register Playwright provider with browser provider registry (entry point)."""
    registry.register(provider_from_config(cfg, env=env))


def register(
    _registry=None,
    *,
    env: EnvironmentConfig | Mapping[str, Any] | None = None,
) -> None:
    """Register Playwright provider for the provider-neutral browser tool."""
    if isinstance(_registry, BrowserProviderRegistry):
        register_browser_provider(_registry, env=env)
        return
    try:
        register_provider(provider_from_config(env=env))
    except ValueError:
        # Provider already registered in global registry.
        return
