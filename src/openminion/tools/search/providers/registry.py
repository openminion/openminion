from __future__ import annotations

from collections.abc import Mapping
import builtins
import logging
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

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


def _iter_entry_points(group: str) -> list[EntryPoint]:
    return sorted(entry_points(group=group), key=lambda ep: ep.name)


def _provider_entry_points(group: str) -> list[EntryPoint]:
    # Preserve the historical package-level monkeypatch seam used by tests and
    # provider harnesses while keeping the registry implementation out of
    # ``providers.__init__``.
    from openminion.tools.search import providers as provider_package

    return provider_package._iter_entry_points(group)


def _is_provider(candidate: Any) -> bool:
    """Heuristic that the candidate quacks like a ``SearchProvider``.

    Mirrors the fetch/browser shape: requires a ``provider_id`` attribute
    and a callable ``search`` method.
    """

    return bool(
        getattr(candidate, "provider_id", None)
        and callable(getattr(candidate, "search", None))
    )


class SearchProviderRegistry:
    """Shared registry for the search provider family."""

    def __init__(self) -> None:
        self._providers: dict[str, SearchProvider] = {}
        self._provider_order: list[str] = []
        self._loaded_entry_points: set[str] = set()
        self._entry_point_statuses: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _normalize(name: str) -> str:
        return str(name or "").strip().lower()

    def register(self, provider: SearchProvider) -> None:
        provider_id = self._normalize(getattr(provider, "provider_id", ""))
        if not provider_id:
            raise ValueError("search provider must define provider_id")
        existing = self._providers.get(provider_id)
        if existing is provider:
            return
        if existing is not None:
            raise ValueError(f"search provider already registered: {provider_id}")
        self._providers[provider_id] = provider
        self._provider_order.append(provider_id)

    def get(self, provider_id: str) -> SearchProvider:
        key = self._normalize(provider_id)
        if key not in self._providers:
            raise KeyError(provider_id)
        return self._providers[key]

    def list(self) -> builtins.list[SearchProvider]:
        return [self._providers[pid] for pid in self._provider_order]

    def list_provider_ids(self) -> builtins.list[str]:
        return list(self._provider_order)

    def entry_point_statuses(self) -> builtins.list[dict[str, Any]]:
        return [
            dict(self._entry_point_statuses[key])
            for key in sorted(self._entry_point_statuses)
        ]

    def load_entry_points(
        self, *, group: str = "openminion.tool.search.providers"
    ) -> builtins.list[str]:
        """Discover and load external search providers."""

        loaded: builtins.list[str] = []
        for ep in _provider_entry_points(group):
            cache_key = f"{group}:{ep.name}:{ep.value}"
            if cache_key in self._loaded_entry_points:
                continue
            self._loaded_entry_points.add(cache_key)
            status = {
                "name": ep.name,
                "module": ep.value,
                "group": group,
                "loaded": False,
                "error": None,
            }
            try:
                target = ep.load()
            except ModuleNotFoundError as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                self._entry_point_statuses[cache_key] = status
                _LOG.warning(
                    "skipping search provider entry point name=%s target=%s reason=%s",
                    ep.name,
                    ep.value,
                    exc,
                )
                continue
            except Exception as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                self._entry_point_statuses[cache_key] = status
                raise

            try:
                hook = _resolve_hook(target, hook_name="register_search_provider")
                if hook is not None:
                    hook(self)
                else:
                    candidate = (
                        target
                        if _is_provider(target)
                        else getattr(target, "provider", None)
                    )
                    if not _is_provider(candidate):
                        raise TypeError(
                            f"search provider entry point '{ep.name}' must expose "
                            "provider object or register_search_provider(registry)"
                        )
                    self.register(cast(SearchProvider, candidate))
            except Exception as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                self._entry_point_statuses[cache_key] = status
                raise

            loaded.append(ep.name)
            status["loaded"] = True
            self._entry_point_statuses[cache_key] = status
        return loaded


_REGISTRY = SearchProviderRegistry()


def provider_registry() -> SearchProviderRegistry:
    """Return the process-wide shared search provider registry."""

    return _REGISTRY


def register_provider(provider: SearchProvider) -> None:
    """Register a provider in the shared registry."""

    _REGISTRY.register(provider)


__all__ = [
    "SearchProvider",
    "SearchProviderError",
    "SearchProviderRegistry",
    "provider_registry",
    "register_provider",
]
