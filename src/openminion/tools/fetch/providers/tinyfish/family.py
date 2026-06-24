"""TinyFish fetch family declaration."""

from openminion.modules.tool.framework import ToolFamilySpec
from openminion.tools.fetch import register_provider

from .provider import provider


def _register_tinyfish_fetch() -> None:
    """Register the TinyFish provider with the parent fetch facade."""

    register_provider(provider)


FETCH_TINYFISH_FAMILY = ToolFamilySpec(
    module_id="fetch_tinyfish",
    is_provider_only=True,
    tools=(),
    provider_registration=_register_tinyfish_fetch,
)


__all__ = ["FETCH_TINYFISH_FAMILY"]
