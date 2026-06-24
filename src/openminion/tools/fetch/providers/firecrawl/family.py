"""Firecrawl fetch family declaration."""

from openminion.modules.tool.framework import ToolFamilySpec
from openminion.tools.fetch import register_provider

from .provider import provider


def _register_firecrawl_fetch() -> None:
    """Register the Firecrawl provider against the parent fetch facade."""

    register_provider(provider)


FETCH_FIRECRAWL_FAMILY = ToolFamilySpec(
    module_id="fetch_firecrawl",
    is_provider_only=True,
    tools=(),
    provider_registration=_register_firecrawl_fetch,
)


__all__ = ["FETCH_FIRECRAWL_FAMILY"]
