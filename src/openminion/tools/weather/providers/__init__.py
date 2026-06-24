from collections.abc import Mapping
from typing import Any, Protocol

from openminion.modules.tool.runtime import RuntimeContext


class WeatherProvider(Protocol):
    provider_id: str

    def lookup(
        self,
        *,
        query_args: Mapping[str, Any],
        extension_args: Mapping[str, Any],
        ctx: RuntimeContext,
    ) -> Mapping[str, Any]: ...

    def healthcheck(self) -> bool: ...


class WeatherProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, WeatherProvider] = {}
        self._order: list[str] = []

    def register(self, provider: WeatherProvider) -> None:
        provider_id = str(getattr(provider, "provider_id", "") or "").strip().lower()
        if not provider_id:
            raise ValueError("weather provider must define provider_id")
        self._providers[provider_id] = provider
        if provider_id not in self._order:
            self._order.append(provider_id)

    def list_provider_ids(self) -> tuple[str, ...]:
        return tuple(self._order)

    def get(self, provider_id: str) -> WeatherProvider | None:
        token = str(provider_id or "").strip().lower()
        if not token:
            return None
        return self._providers.get(token)


_PROVIDER_REGISTRY = WeatherProviderRegistry()


def provider_registry() -> WeatherProviderRegistry:
    return _PROVIDER_REGISTRY


def register_provider(provider: WeatherProvider) -> None:
    _PROVIDER_REGISTRY.register(provider)


__all__ = [
    "WeatherProvider",
    "WeatherProviderRegistry",
    "provider_registry",
    "register_provider",
]
