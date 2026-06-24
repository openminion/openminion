"""Skill selection normalization helpers."""

from __future__ import annotations

from typing import Any

SKILL_SELECTION_AUTO = "auto"


def _normalized_skill_list(value: Any, *, skip_auto: bool) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    skills: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = str(item or "").strip()
        lowered = normalized.lower()
        if not normalized or (skip_auto and lowered == SKILL_SELECTION_AUTO):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        skills.append(normalized)
    return skills


def normalize_skill_value(value: Any) -> str | list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.lower() == SKILL_SELECTION_AUTO:
            return SKILL_SELECTION_AUTO
        return normalized
    skills = _normalized_skill_list(value, skip_auto=True)
    return skills or None


def normalize_skill_catalog(value: Any) -> list[str]:
    return _normalized_skill_list(value, skip_auto=False)


def skill_value_to_payload(value: str | list[str] | None) -> str | list[str] | None:
    normalized = normalize_skill_value(value)
    if normalized is None:
        return None
    return list(normalized) if isinstance(normalized, list) else normalized


def skill_value_to_list(value: str | list[str] | None) -> tuple[bool, list[str]]:
    normalized = normalize_skill_value(value)
    if normalized is None:
        return False, []
    if isinstance(normalized, list):
        return False, list(normalized)
    if normalized == SKILL_SELECTION_AUTO:
        return True, []
    return False, [normalized]


def is_skill_auto(value: str | list[str] | None) -> bool:
    return normalize_skill_value(value) == SKILL_SELECTION_AUTO


__all__ = [
    "SKILL_SELECTION_AUTO",
    "is_skill_auto",
    "normalize_skill_catalog",
    "normalize_skill_value",
    "skill_value_to_list",
    "skill_value_to_payload",
]
