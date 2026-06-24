"""Public exports for tools search providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Callable, Mapping, Protocol, TYPE_CHECKING, runtime_checkable

from openminion.tools.config import resolve_provider_register_hook as _resolve_hook

if TYPE_CHECKING:  # pragma: no cover
    from openminion.modules.tool.runtime import RuntimeContext


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchProviderError(RuntimeError):
    message: str
    code: str = "UPSTREAM_ERROR"
    details: Mapping[str, Any] | None = None

    def __str__(self) -> str:
        return str(self.message)


@runtime_checkable
class SearchProvider(Protocol):
    provider_id: str
    display_name: str

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: "RuntimeContext",
    ) -> Mapping[str, Any]: ...

    def healthcheck(self, ctx: "RuntimeContext | None" = None) -> bool: ...


ProviderRegisterFn = Callable[["SearchProviderRegistry"], None]


def _iter_entry_points(group: str) -> list[EntryPoint]:
    """Return deterministic, name-sorted entry points for ``group``.

    Falls back to the dict-style API on Python <3.10 entry_points calls.
    """

    try:
        eps = entry_points(group=group)
        return sorted(eps, key=lambda ep: ep.name)
    except TypeError:
        all_eps = entry_points()
        fallback_eps = all_eps.get(group, [])
        return sorted(fallback_eps, key=lambda ep: ep.name)


def _is_provider(candidate: Any) -> bool:
    """Heuristic that the candidate quacks like a ``SearchProvider``.

    Mirrors the fetch/browser shape: requires a ``provider_id`` attribute
    and a callable ``search`` method.
    """

    return bool(
        candidate is not None
        and getattr(candidate, "provider_id", None)
        and callable(getattr(candidate, "search", None))
    )


class SearchProviderRegistry:
    """Shared registry for the search provider family."""

    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}
        self._provider_order: list[str] = []
        self._loaded_entry_points: set[str] = set()

    @staticmethod
    def _normalize(name: str) -> str:
        return str(name or "").strip().lower()

    def register(self, provider: SearchProvider) -> None:
        provider_id = self._normalize(getattr(provider, "provider_id", ""))
        if not provider_id:
            raise ValueError("search provider must define provider_id")
        if provider_id in self._providers:
            # Idempotent re-registration: keep the first instance so the
            return
        self._providers[provider_id] = provider
        self._provider_order.append(provider_id)

    def get(self, provider_id: str) -> SearchProvider:
        key = self._normalize(provider_id)
        if key not in self._providers:
            raise KeyError(provider_id)
        return self._providers[key]

    def list(self) -> list[SearchProvider]:
        return [self._providers[pid] for pid in self._provider_order]

    def list_provider_ids(self) -> list[str]:
        return list(self._provider_order)

    def load_entry_points(
        self, *, group: str = "openminion.tool.search.providers"
    ) -> list[str]:
        """Discover and load external search providers."""

        loaded: list[str] = []
        for ep in _iter_entry_points(group):
            cache_key = f"{group}:{ep.name}:{ep.value}"
            if cache_key in self._loaded_entry_points:
                continue
            try:
                target = ep.load()
            except ModuleNotFoundError as exc:
                _LOG.warning(
                    "skipping search provider entry point name=%s target=%s reason=%s",
                    ep.name,
                    ep.value,
                    exc,
                )
                continue

            hook = _resolve_hook(target, hook_name="register_search_provider")
            if hook is not None:
                hook(self)
                self._loaded_entry_points.add(cache_key)
                loaded.append(ep.name)
                continue

            if _is_provider(target):
                try:
                    self.register(target)
                except ValueError:
                    pass
                self._loaded_entry_points.add(cache_key)
                loaded.append(ep.name)
                continue

            candidate = getattr(target, "provider", None)
            if _is_provider(candidate):
                try:
                    self.register(candidate)
                except ValueError:
                    pass
                self._loaded_entry_points.add(cache_key)
                loaded.append(ep.name)
                continue

            raise TypeError(
                f"search provider entry point '{ep.name}' must expose provider "
                "object or register_search_provider(registry)"
            )
        return loaded


_REGISTRY = SearchProviderRegistry()


def provider_registry() -> SearchProviderRegistry:
    """Return the process-wide shared search provider registry."""

    return _REGISTRY


def register_provider(provider: SearchProvider) -> None:
    """Convenience: register a provider into the shared registry.

    Idempotent — duplicate ``provider_id`` is a no-op (matches fetch/browser).
    """

    try:
        _REGISTRY.register(provider)
    except ValueError:
        return


__all__ = [
    "SearchProvider",
    "SearchProviderError",
    "SearchProviderRegistry",
    "provider_registry",
    "register_provider",
]
