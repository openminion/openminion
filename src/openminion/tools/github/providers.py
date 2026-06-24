from collections.abc import Mapping
from typing import Any, Protocol

from .constants import DEFAULT_GITHUB_PROVIDER_ID


class GithubProvider(Protocol):
    """V1 GitHub provider contract."""

    provider_id: str

    def list_prs(self, *, args: Mapping[str, Any], ctx: Any) -> Mapping[str, Any]: ...

    def fetch_pr(self, *, args: Mapping[str, Any], ctx: Any) -> Mapping[str, Any]: ...

    def fetch_diff(self, *, args: Mapping[str, Any], ctx: Any) -> Mapping[str, Any]: ...

    def fetch_comments(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> Mapping[str, Any]: ...

    def fetch_checks(
        self, *, args: Mapping[str, Any], ctx: Any
    ) -> Mapping[str, Any]: ...

    def healthcheck(self) -> bool: ...


class _GithubProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, GithubProvider] = {}

    def register(self, provider: GithubProvider) -> None:
        self._providers[provider.provider_id] = provider

    def get(self, provider_id: str) -> GithubProvider | None:
        return self._providers.get(provider_id)

    def default(self) -> GithubProvider | None:
        return self._providers.get(DEFAULT_GITHUB_PROVIDER_ID)

    def reset(self) -> None:
        self._providers.clear()


_REGISTRY = _GithubProviderRegistry()


def provider_registry() -> _GithubProviderRegistry:
    return _REGISTRY


def register_provider(provider: GithubProvider) -> None:
    _REGISTRY.register(provider)


__all__ = [
    "GithubProvider",
    "provider_registry",
    "register_provider",
]
