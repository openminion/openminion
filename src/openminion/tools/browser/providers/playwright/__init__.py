"""Public exports for tools browser providers playwright."""

from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

from .registrar import REGISTRAR
from .plugin import provider_from_config, register
from .provider import PlaywrightProvider, PlaywrightProviderConfig

REGISTRAR: ToolModuleRegistrar

__all__ = [
    "REGISTRAR",
    "PlaywrightProvider",
    "PlaywrightProviderConfig",
    "provider_from_config",
    "register",
]
