"""Factory and registry helpers for lower durable-memory backends."""

from dataclasses import dataclass
from typing import Any, Callable

from .config import KnowledgeBackendConfig
from ..errors import InvalidArgumentError
from .interfaces import KnowledgeBackend, ensure_backend_compatibility

KnowledgeBackendFactory = Callable[..., KnowledgeBackend]


@dataclass(frozen=True)
class ResolvedKnowledgeBackendFactory:
    provider: str
    factory: KnowledgeBackendFactory
    config: KnowledgeBackendConfig


_BACKEND_FACTORIES: dict[str, KnowledgeBackendFactory] = {}


def register_backend_factory(
    provider: str,
    factory: KnowledgeBackendFactory,
) -> None:
    normalized = str(provider or "").strip().lower()
    if not normalized:
        raise InvalidArgumentError("backend provider name is required")
    _BACKEND_FACTORIES[normalized] = factory


def get_registered_backend_factory(provider: str) -> KnowledgeBackendFactory:
    normalized = str(provider or "").strip().lower()
    try:
        return _BACKEND_FACTORIES[normalized]
    except KeyError as exc:
        known = ", ".join(sorted(_BACKEND_FACTORIES)) or "<none>"
        raise InvalidArgumentError(
            f"No backend factory registered for {provider!r}; known providers: {known}."
        ) from exc


def list_registered_backend_factories() -> tuple[str, ...]:
    return tuple(sorted(_BACKEND_FACTORIES))


def resolve_backend_factory(
    *,
    config: KnowledgeBackendConfig,
) -> ResolvedKnowledgeBackendFactory:
    return ResolvedKnowledgeBackendFactory(
        provider=config.provider,
        factory=get_registered_backend_factory(config.provider),
        config=config,
    )


def instantiate_backend(
    *,
    config: KnowledgeBackendConfig,
    **kwargs: Any,
) -> KnowledgeBackend:
    backend = get_registered_backend_factory(config.provider)(config=config, **kwargs)
    ensure_backend_compatibility(backend, strict=True)
    return backend


__all__ = [
    "KnowledgeBackendFactory",
    "ResolvedKnowledgeBackendFactory",
    "get_registered_backend_factory",
    "instantiate_backend",
    "list_registered_backend_factories",
    "register_backend_factory",
    "resolve_backend_factory",
]
