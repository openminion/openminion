"""Public exports for tools search providers serper."""

from typing import TYPE_CHECKING

from .family import SEARCH_SERPER_FAMILY
from .plugin import register_search_provider
from .provider import SerperSearchProvider, SerperSearchProviderConfig
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = [
    "REGISTRAR",
    "SEARCH_SERPER_FAMILY",
    "SerperSearchProvider",
    "SerperSearchProviderConfig",
    "register_search_provider",
]
