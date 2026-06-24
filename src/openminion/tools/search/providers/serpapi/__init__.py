"""Public exports for tools search providers serpapi."""

from typing import TYPE_CHECKING

from .plugin import register, register_search_provider
from .provider import SerpApiSearchProvider, SerpApiSearchProviderConfig
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = [
    "REGISTRAR",
    "SerpApiSearchProvider",
    "SerpApiSearchProviderConfig",
    "register",
    "register_search_provider",
]
