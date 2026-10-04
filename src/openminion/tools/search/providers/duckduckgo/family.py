from openminion.modules.tool.framework import ToolFamilySpec
from openminion.tools.search import register_provider

from .provider import DuckDuckGoSearchProvider

_PROVIDER = DuckDuckGoSearchProvider()


def _register_duckduckgo_search() -> None:
    register_provider(_PROVIDER)


SEARCH_DUCKDUCKGO_FAMILY = ToolFamilySpec(
    module_id="search.duckduckgo",
    is_provider_only=True,
    tools=(),
    provider_registration=_register_duckduckgo_search,
)


__all__ = ["SEARCH_DUCKDUCKGO_FAMILY"]
