from __future__ import annotations

from importlib import metadata
from typing import Protocol

from .models import (
    AnimationDiagnostic,
    AnimationResolution,
    AnimationSpec,
    AnimationSpecError,
    coerce_animation_spec,
    validate_animation_spec,
)

BUILTIN_PROVIDER_ID = "openminion"
BUILTIN_ANIMATION_NAME = "braille"
ENTRY_POINT_GROUP = "openminion.cli.animation_providers"
BUILTIN_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
BUILTIN_INTERVAL_MS = 80


class AnimationProvider(Protocol):
    provider_id: str

    def names(self) -> tuple[str, ...]: ...

    def get(self, name: str) -> object: ...


class BuiltInAnimationProvider:
    provider_id = BUILTIN_PROVIDER_ID

    def names(self) -> tuple[str, ...]:
        return (BUILTIN_ANIMATION_NAME,)

    def get(self, name: str) -> AnimationSpec:
        if name != BUILTIN_ANIMATION_NAME:
            raise KeyError(name)
        return validate_animation_spec(
            AnimationSpec(
                provider_id=BUILTIN_PROVIDER_ID,
                name=BUILTIN_ANIMATION_NAME,
                frames=BUILTIN_FRAMES,
                interval_ms=BUILTIN_INTERVAL_MS,
            )
        )


class AnimationRegistry:
    def __init__(self, providers: tuple[AnimationProvider, ...] = ()) -> None:
        self._providers: dict[str, AnimationProvider] = {}
        self._entry_points_loaded = False
        self._diagnostics: list[AnimationDiagnostic] = []
        self.register(BuiltInAnimationProvider())
        for provider in providers:
            self.register(provider)

    @property
    def diagnostics(self) -> tuple[AnimationDiagnostic, ...]:
        return tuple(self._diagnostics)

    def register(self, provider: AnimationProvider) -> None:
        provider_id = str(getattr(provider, "provider_id", "") or "").strip().lower()
        if not provider_id:
            raise AnimationSpecError("empty_provider_id", "provider_id is required")
        if provider_id in self._providers:
            raise AnimationSpecError(
                "duplicate_provider_id",
                f"animation provider {provider_id!r} is already registered",
            )
        names = getattr(provider, "names", None)
        getter = getattr(provider, "get", None)
        if not callable(names) or not callable(getter):
            raise AnimationSpecError(
                "malformed_provider",
                f"animation provider {provider_id!r} must expose names() and get()",
            )
        self._providers[provider_id] = provider

    def provider_ids(self, *, discover: bool = False) -> tuple[str, ...]:
        if discover:
            self.discover_entry_points()
        return tuple(sorted(self._providers))

    def names(self, provider_id: str, *, discover: bool = False) -> tuple[str, ...]:
        provider = self._provider(provider_id, discover=discover)
        try:
            return tuple(str(name).strip().lower() for name in provider.names())
        except Exception as exc:
            raise AnimationSpecError(
                "provider_names_failed",
                f"{provider_id}: {exc}",
            ) from exc

    def get(
        self,
        provider_id: str,
        name: str,
        *,
        discover: bool = False,
    ) -> AnimationSpec:
        provider = self._provider(provider_id, discover=discover)
        try:
            raw = provider.get(name)
        except KeyError as exc:
            raise AnimationSpecError(
                "unknown_animation",
                f"{provider_id}:{name} is not available",
            ) from exc
        except Exception as exc:
            raise AnimationSpecError(
                "provider_get_failed",
                f"{provider_id}:{name}: {exc}",
            ) from exc
        return coerce_animation_spec(raw, provider_id=provider_id)

    def resolve(
        self,
        provider_id: str,
        name: str,
        *,
        source: str,
        discover: bool = False,
        allow_fallback: bool = True,
    ) -> AnimationResolution:
        try:
            spec = self.get(provider_id, name, discover=discover)
            return AnimationResolution(spec=spec, source=source)
        except AnimationSpecError as exc:
            diagnostic = AnimationDiagnostic(
                reason=exc.reason,
                provider_id=provider_id,
                name=name,
                detail=exc.detail,
            )
            if not allow_fallback:
                raise
            fallback = self.get(BUILTIN_PROVIDER_ID, BUILTIN_ANIMATION_NAME)
            return AnimationResolution(
                spec=fallback,
                source=source,
                fallback_reason=diagnostic.reason,
                diagnostic=diagnostic,
            )

    def discover_entry_points(self) -> None:
        if self._entry_points_loaded:
            return
        self._entry_points_loaded = True
        for entry_point in metadata.entry_points(group=ENTRY_POINT_GROUP):
            self._load_entry_point(entry_point)

    def _provider(self, provider_id: str, *, discover: bool) -> AnimationProvider:
        normalized = str(provider_id or "").strip().lower()
        if discover and normalized not in self._providers:
            self.discover_entry_points()
        provider = self._providers.get(normalized)
        if provider is None:
            raise AnimationSpecError(
                "unknown_provider",
                f"animation provider {normalized!r} is not available",
            )
        return provider

    def _load_entry_point(self, entry_point: metadata.EntryPoint) -> None:
        try:
            loaded = entry_point.load()
            provider = loaded() if callable(loaded) else loaded
            self.register(provider)
        except Exception as exc:
            self._diagnostics.append(
                AnimationDiagnostic(
                    reason="entry_point_load_failed",
                    provider_id=entry_point.name,
                    detail=str(exc),
                )
            )


def default_animation_registry() -> AnimationRegistry:
    return AnimationRegistry()


__all__ = [
    "BUILTIN_ANIMATION_NAME",
    "BUILTIN_FRAMES",
    "BUILTIN_INTERVAL_MS",
    "BUILTIN_PROVIDER_ID",
    "ENTRY_POINT_GROUP",
    "AnimationProvider",
    "AnimationRegistry",
    "BuiltInAnimationProvider",
    "default_animation_registry",
]
