from __future__ import annotations

from .models import AnimationResolution, AnimationSpecError
from .registry import (
    BUILTIN_ANIMATION_NAME,
    BUILTIN_PROVIDER_ID,
    AnimationRegistry,
    default_animation_registry,
)


class AnimationSelectionError(ValueError):
    """Raised for explicit animation selections that cannot be honored."""


def parse_animation_token(
    token: str,
    *,
    default_provider: str = BUILTIN_PROVIDER_ID,
) -> tuple[str, str]:
    raw = str(token or "").strip().lower()
    if not raw:
        return (default_provider, BUILTIN_ANIMATION_NAME)
    if ":" in raw:
        provider_id, name = raw.split(":", 1)
        return (provider_id.strip(), name.strip())
    return (default_provider, raw)


def resolve_focus_animation(
    args: object,
    *,
    registry: AnimationRegistry | None = None,
) -> AnimationResolution:
    active_registry = registry or default_animation_registry()
    explicit_provider = str(getattr(args, "animation_provider", "") or "").strip()
    explicit_name = str(getattr(args, "animation", "") or "").strip()
    if explicit_name and ":" in explicit_name and not explicit_provider:
        provider_id, name = parse_animation_token(explicit_name)
        return _resolve_explicit(active_registry, provider_id, name)
    if explicit_provider or explicit_name:
        provider_id = explicit_provider or BUILTIN_PROVIDER_ID
        name = explicit_name or BUILTIN_ANIMATION_NAME
        return _resolve_explicit(active_registry, provider_id, name)

    from openminion.cli.ux.verbosity import read_focus_preferences

    prefs = read_focus_preferences()
    persisted_provider = prefs.get("animation_provider", "")
    persisted_name = prefs.get("animation", "")
    if persisted_provider or persisted_name:
        return active_registry.resolve(
            persisted_provider or BUILTIN_PROVIDER_ID,
            persisted_name or BUILTIN_ANIMATION_NAME,
            source="preference",
            discover=True,
            allow_fallback=True,
        )
    return active_registry.resolve(
        BUILTIN_PROVIDER_ID,
        BUILTIN_ANIMATION_NAME,
        source="default",
        discover=False,
        allow_fallback=False,
    )


def _resolve_explicit(
    registry: AnimationRegistry,
    provider_id: str,
    name: str,
) -> AnimationResolution:
    try:
        return registry.resolve(
            provider_id,
            name,
            source="flag",
            discover=True,
            allow_fallback=False,
        )
    except AnimationSpecError as exc:
        raise AnimationSelectionError(str(exc)) from exc


__all__ = ["AnimationSelectionError", "parse_animation_token", "resolve_focus_animation"]
