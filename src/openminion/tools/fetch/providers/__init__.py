from __future__ import annotations

import logging
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Callable

from openminion.tools.config import resolve_provider_register_hook as _resolve_hook
from ..interfaces import FetchProviderProtocol

_LOG = logging.getLogger(__name__)

ProviderRegisterFn = Callable[["FetchProviderRegistry"], None]


def _iter_entry_points(group: str) -> list[EntryPoint]:
    try:
        eps = entry_points(group=group)
        return sorted(eps, key=lambda ep: ep.name)
    except TypeError:
        all_eps = entry_points()
        fallback_eps = all_eps.get(group, [])
        return sorted(fallback_eps, key=lambda ep: ep.name)


def _is_provider(candidate: Any) -> bool:
    return bool(
        candidate is not None
        and getattr(candidate, "name", None)
        and callable(getattr(candidate, "fetch", None))
    )


class FetchProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, FetchProviderProtocol] = {}
        self._loaded_entry_points: set[str] = set()

    def register(self, provider: FetchProviderProtocol) -> None:
        name = str(getattr(provider, "name", "")).strip().lower()
        if not name:
            raise ValueError("fetch provider name is required")
        if name in self._providers:
            raise ValueError(f"fetch provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> FetchProviderProtocol:
        key = str(name or "").strip().lower()
        if key not in self._providers:
            raise KeyError(key)
        return self._providers[key]

    def list(self) -> list[FetchProviderProtocol]:
        return [self._providers[key] for key in sorted(self._providers)]

    def list_names(self) -> list[str]:
        return sorted(self._providers)

    def load_entry_points(
        self, *, group: str = "openminion.tool.fetch.providers"
    ) -> list[str]:
        loaded: list[str] = []
        for ep in _iter_entry_points(group):
            cache_key = f"{group}:{ep.name}:{ep.value}"
            if cache_key in self._loaded_entry_points:
                continue

            def remember(provider: FetchProviderProtocol) -> None:
                try:
                    self.register(provider)
                except ValueError:
                    pass
                self._loaded_entry_points.add(cache_key)
                loaded.append(ep.name)

            try:
                target = ep.load()
            except ModuleNotFoundError as exc:
                _LOG.warning(
                    "skipping fetch provider entry point name=%s target=%s reason=%s",
                    ep.name,
                    ep.value,
                    exc,
                )
                continue

            hook = _resolve_hook(target, hook_name="register_fetch_provider")
            if hook is not None:
                hook(self)
                self._loaded_entry_points.add(cache_key)
                loaded.append(ep.name)
                continue

            if _is_provider(target):
                remember(target)
                continue

            candidate = getattr(target, "provider", None)
            if _is_provider(candidate):
                remember(candidate)
                continue

            raise TypeError(
                f"fetch provider entry point '{ep.name}' must expose provider "
                "object or register_fetch_provider(registry)"
            )
        return loaded


_REGISTRY = FetchProviderRegistry()


def provider_registry() -> FetchProviderRegistry:
    return _REGISTRY


def register_provider(provider: FetchProviderProtocol) -> None:
    try:
        _REGISTRY.register(provider)
    except ValueError:
        return


__all__ = ["FetchProviderRegistry", "provider_registry", "register_provider"]
