"""Public exports for tools search providers firecrawl."""

from typing import TYPE_CHECKING

from .plugin import register, register_search_provider
from .provider import FirecrawlSearchProvider, FirecrawlSearchProviderConfig
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = [
    "FirecrawlSearchProvider",
    "FirecrawlSearchProviderConfig",
    "REGISTRAR",
    "register",
    "register_search_provider",
]
