from .config import DuckDuckGoSearchProviderConfig
from .family import SEARCH_DUCKDUCKGO_FAMILY
from .plugin import register_search_provider
from .provider import DuckDuckGoSearchProvider
from .registrar import REGISTRAR

__all__ = [
    "DuckDuckGoSearchProvider",
    "DuckDuckGoSearchProviderConfig",
    "REGISTRAR",
    "SEARCH_DUCKDUCKGO_FAMILY",
    "register_search_provider",
]
