from typing import TYPE_CHECKING

from .family import SEARCH_TINYFISH_FAMILY
from .plugin import register_search_provider
from .provider import TinyFishSearchProvider, TinyFishSearchProviderConfig
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = [
    "REGISTRAR",
    "SEARCH_TINYFISH_FAMILY",
    "TinyFishSearchProvider",
    "TinyFishSearchProviderConfig",
    "register_search_provider",
]
