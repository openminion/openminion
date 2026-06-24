from openminion.tools.fetch import register_provider

from .provider import provider


def register_fetch_provider(registry: object) -> None:
    del registry
    register_provider(provider)


__all__ = ["register_fetch_provider"]
