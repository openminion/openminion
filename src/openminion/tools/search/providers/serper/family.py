"""Serper search provider family."""

from openminion.modules.tool.framework import ToolFamilySpec
from openminion.tools.search import register_provider

from .provider import SerperSearchProvider


def _register_serper_search() -> None:
    register_provider(SerperSearchProvider())


SEARCH_SERPER_FAMILY = ToolFamilySpec(
    module_id="search.serper",
    is_provider_only=True,
    tools=(),
    provider_registration=_register_serper_search,
)


__all__ = ["SEARCH_SERPER_FAMILY"]
