"""Public exports for tools search providers brave."""

from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

from .registrar import REGISTRAR
from .plugin import BraveSearchFacadeProvider, register, register_search_provider
from .provider import (
    BraveSearchError,
    BraveSearchProvider,
    BraveSearchProviderConfig,
    clamp_count,
    clamp_offset,
)

REGISTRAR: ToolModuleRegistrar

__all__ = [
    "REGISTRAR",
    "BraveSearchFacadeProvider",
    "BraveSearchError",
    "BraveSearchProvider",
    "BraveSearchProviderConfig",
    "clamp_count",
    "clamp_offset",
    "register",
    "register_search_provider",
]
