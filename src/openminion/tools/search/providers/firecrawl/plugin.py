from openminion.tools.search import register_provider

from .provider import FirecrawlSearchProvider


def register(registry: object | None = None) -> None:
    del registry
    register_provider(FirecrawlSearchProvider())


register_search_provider = register


__all__ = ["register", "register_search_provider"]
