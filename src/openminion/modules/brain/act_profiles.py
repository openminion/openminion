from typing import Any

from .constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_ACT_PROFILE_ORCHESTRATE,
    BRAIN_ACT_PROFILE_RESEARCH,
)

_ALLOWED_ACT_PROFILES = frozenset(
    {
        str(BRAIN_ACT_PROFILE_GENERAL),
        str(BRAIN_ACT_PROFILE_CODING),
        str(BRAIN_ACT_PROFILE_RESEARCH),
        str(BRAIN_ACT_PROFILE_ORCHESTRATE),
    }
)


def normalize_default_act_profile(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text or text == "auto":
        return None
    if text in _ALLOWED_ACT_PROFILES:
        return text
    return None


def fixed_act_profile_from_profile(profile: Any) -> str | None:
    return normalize_default_act_profile(
        getattr(profile, "default_act_profile", None),
    )


def fixed_act_profile_from_context(context: dict[str, Any] | None) -> str | None:
    if not isinstance(context, dict):
        return None
    hints = context.get("hints")
    if not isinstance(hints, dict):
        return None
    return normalize_default_act_profile(hints.get("default_act_profile"))


__all__ = [
    "_ALLOWED_ACT_PROFILES",
    "fixed_act_profile_from_context",
    "fixed_act_profile_from_profile",
    "normalize_default_act_profile",
]
