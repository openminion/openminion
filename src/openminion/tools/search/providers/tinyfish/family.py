from openminion.modules.tool.framework import ToolFamilySpec
from openminion.tools.search import register_provider

from .provider import TinyFishSearchProvider


def _register_tinyfish_search() -> None:
    register_provider(TinyFishSearchProvider())


SEARCH_TINYFISH_FAMILY = ToolFamilySpec(
    module_id="search.tinyfish",
    is_provider_only=True,
    tools=(),
    provider_registration=_register_tinyfish_search,
)


__all__ = ["SEARCH_TINYFISH_FAMILY"]
