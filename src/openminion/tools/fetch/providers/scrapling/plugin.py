from openminion.tools.fetch import register_provider

from .provider import provider


def register(registry: object | None = None) -> None:
    del registry
    register_provider(provider)


def register_fetch_provider(registry: object) -> None:
    register(registry)


__all__ = ["register", "register_fetch_provider"]
