from collections.abc import Mapping
from typing import Any, Protocol


class IpProvider(Protocol):
    provider_id: str

    def resolve_public(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> Mapping[str, Any]: ...

    def resolve_local(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> Mapping[str, Any]: ...

    def healthcheck(self) -> bool: ...


class IpProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, IpProvider] = {}
        self._order: list[str] = []

    def register(self, provider: IpProvider) -> None:
        provider_id = str(getattr(provider, "provider_id", "") or "").strip().lower()
        if not provider_id:
            raise ValueError("ip provider must define provider_id")
        self._providers[provider_id] = provider
        if provider_id not in self._order:
            self._order.append(provider_id)

    def get(self, provider_id: str) -> IpProvider | None:
        token = str(provider_id or "").strip().lower()
        if not token:
            return None
        return self._providers.get(token)

    def list_provider_ids(self) -> tuple[str, ...]:
        return tuple(self._order)

    def clear(self) -> None:
        self._providers.clear()
        self._order.clear()


_PROVIDER_REGISTRY = IpProviderRegistry()


def provider_registry() -> IpProviderRegistry:
    return _PROVIDER_REGISTRY


def register_provider(provider: IpProvider) -> None:
    _PROVIDER_REGISTRY.register(provider)


def _reset_provider_registry_for_tests() -> None:
    _PROVIDER_REGISTRY.clear()


__all__ = [
    "IpProvider",
    "IpProviderRegistry",
    "provider_registry",
    "register_provider",
]
