from typing import TYPE_CHECKING

from .plugin import list_provider_ids, register, register_provider
from .providers import SearchProvider, SearchProviderError
from .registrar import REGISTRAR as _REGISTRAR

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = [
    "REGISTRAR",
    "SearchProvider",
    "SearchProviderError",
    "list_provider_ids",
    "register",
    "register_provider",
]
