"""Provider registry for the knowledge-graph layer."""

from dataclasses import dataclass
from typing import Callable, Mapping

from .config import KnowledgeGraphProviderConfig
from .constants import (
    CAPABILITY_DURABLE_MEMORY,
    LAYER_THIRD_BRAIN,
)
from .errors import (
    DisabledProviderError,
    DuplicateProviderError,
    HybridDurableMemoryError,
    MissingRequiredCapabilityError,
    UnknownProviderError,
)
from .interfaces import KnowledgeGraphSource

KnowledgeGraphProviderFactory = Callable[..., KnowledgeGraphSource]


@dataclass(frozen=True)
class ResolvedKnowledgeGraphProvider:
    """A registry entry resolved against a provider config."""

    name: str
    provider: str
    factory: KnowledgeGraphProviderFactory
    config: KnowledgeGraphProviderConfig


class KnowledgeGraphRegistry:
    """In-memory registry of knowledge-graph provider factories.

    Each registry instance is independent so tests and multi-runtime callers
    can hold isolated registries. Factories are stored by ``provider`` key (the
    implementation name, e.g. ``"graphify"``); a single factory can back many
    operator-named provider configs.
    """

    def __init__(self) -> None:
        self._factories: dict[str, KnowledgeGraphProviderFactory] = {}

    def register(
        self,
        provider: str,
        factory: KnowledgeGraphProviderFactory,
        *,
        overwrite: bool = False,
    ) -> None:
        normalized = _normalize_provider_name(provider)
        if not overwrite and normalized in self._factories:
            raise DuplicateProviderError(
                f"Provider {provider!r} is already registered",
                details={"provider": normalized},
            )
        self._factories[normalized] = factory

    def get(self, provider: str) -> KnowledgeGraphProviderFactory:
        normalized = _normalize_provider_name(provider)
        try:
            return self._factories[normalized]
        except KeyError as exc:
            known = ", ".join(sorted(self._factories)) or "<none>"
            raise UnknownProviderError(
                f"No knowledge-graph provider factory registered for {provider!r}; "
                f"known providers: {known}.",
                details={"provider": normalized, "known": sorted(self._factories)},
            ) from exc

    def is_registered(self, provider: str) -> bool:
        normalized = _normalize_provider_name(provider)
        return normalized in self._factories

    def list_registered(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def resolve(
        self,
        provider_config: KnowledgeGraphProviderConfig,
    ) -> ResolvedKnowledgeGraphProvider:
        if not provider_config.enabled:
            raise DisabledProviderError(
                f"Provider {provider_config.name!r} is disabled in config",
                details={
                    "name": provider_config.name,
                    "provider": provider_config.provider,
                },
            )
        factory = self.get(provider_config.provider)
        return ResolvedKnowledgeGraphProvider(
            name=provider_config.name,
            provider=provider_config.provider,
            factory=factory,
            config=provider_config,
        )

    def instantiate(
        self,
        provider_config: KnowledgeGraphProviderConfig,
        *,
        layer: str,
        **kwargs: object,
    ) -> KnowledgeGraphSource:
        resolved = self.resolve(provider_config)
        source = resolved.factory(config=provider_config, layer=layer, **kwargs)
        validate_provider_capabilities(source, provider_config, layer=layer)
        return source


def _normalize_provider_name(provider: str) -> str:
    text = str(provider or "").strip().lower()
    if not text:
        raise UnknownProviderError(
            "provider name is required",
            details={"provider": provider},
        )
    return text


def validate_provider_capabilities(
    source: KnowledgeGraphSource,
    config: KnowledgeGraphProviderConfig,
    *,
    layer: str,
) -> None:
    """Validate provider capabilities helper."""
    advertised = source.capabilities.advertised
    missing = [cap for cap in config.required_capabilities if cap not in advertised]
    if missing:
        raise MissingRequiredCapabilityError(
            f"Provider {config.name!r} does not advertise required capabilities: "
            + ", ".join(missing),
            details={
                "name": config.name,
                "provider": config.provider,
                "missing": missing,
                "advertised": sorted(advertised),
            },
        )
    if layer == LAYER_THIRD_BRAIN and CAPABILITY_DURABLE_MEMORY in advertised:
        raise HybridDurableMemoryError(
            f"Provider {config.name!r} is provider but advertises durable_memory; "
            "use promotes_to_durable and delegate writes to a second-brain provider.",
            details={
                "name": config.name,
                "provider": config.provider,
                "layer": layer,
            },
        )


def report_optional_capabilities(
    source: KnowledgeGraphSource,
    config: KnowledgeGraphProviderConfig,
) -> Mapping[str, bool]:
    """Report which optional capabilities the source actually advertises."""
    advertised = source.capabilities.advertised
    return {cap: cap in advertised for cap in config.optional_capabilities}
