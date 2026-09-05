from __future__ import annotations

import logging
import builtins
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Callable, cast

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
        fallback_eps: builtins.list[EntryPoint] = all_eps.get(group, [])
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
        self._entry_point_statuses: dict[str, dict[str, Any]] = {}

    def register(self, provider: FetchProviderProtocol) -> None:
        name = str(getattr(provider, "name", "")).strip().lower()
        if not name:
            raise ValueError("fetch provider name is required")
        existing = self._providers.get(name)
        if existing is provider:
            return
        if existing is not None:
            raise ValueError(f"fetch provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> FetchProviderProtocol:
        key = str(name or "").strip().lower()
        if key not in self._providers:
            raise KeyError(key)
        return self._providers[key]

    def list(self) -> builtins.list[FetchProviderProtocol]:
        return [self._providers[key] for key in sorted(self._providers)]

    def list_names(self) -> builtins.list[str]:
        return sorted(self._providers)

    def entry_point_statuses(self) -> builtins.list[dict[str, Any]]:
        return [
            dict(self._entry_point_statuses[key])
            for key in sorted(self._entry_point_statuses)
        ]

    def load_entry_points(
        self, *, group: str = "openminion.tool.fetch.providers"
    ) -> builtins.list[str]:
        loaded: builtins.list[str] = []
        for ep in _iter_entry_points(group):
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

            def remember(provider: FetchProviderProtocol) -> None:
                self.register(provider)
                loaded.append(ep.name)

            try:
                target = ep.load()
            except ModuleNotFoundError as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                self._entry_point_statuses[cache_key] = status
                _LOG.warning(
                    "skipping fetch provider entry point name=%s target=%s reason=%s",
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
                hook = _resolve_hook(target, hook_name="register_fetch_provider")
                if hook is not None:
                    hook(self)
                    loaded.append(ep.name)
                elif _is_provider(target):
                    remember(target)
                else:
                    candidate = getattr(target, "provider", None)
                    if not _is_provider(candidate):
                        raise TypeError(
                            f"fetch provider entry point '{ep.name}' must expose "
                            "provider object or register_fetch_provider(registry)"
                        )
                    remember(cast(FetchProviderProtocol, candidate))
            except Exception as exc:
                status["error"] = f"{type(exc).__name__}: {exc}"
                self._entry_point_statuses[cache_key] = status
                raise
            status["loaded"] = True
            self._entry_point_statuses[cache_key] = status
        return loaded


_REGISTRY = FetchProviderRegistry()


def provider_registry() -> FetchProviderRegistry:
    return _REGISTRY


def register_provider(provider: FetchProviderProtocol) -> None:
    _REGISTRY.register(provider)


__all__ = ["FetchProviderRegistry", "provider_registry", "register_provider"]
