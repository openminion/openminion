from openminion.tools.search import register_provider

from .provider import SerpApiSearchProvider

_PROVIDER = SerpApiSearchProvider()


def register(registry: object | None = None) -> None:
    del registry
    register_provider(_PROVIDER)


register_search_provider = register


__all__ = ["register", "register_search_provider"]
