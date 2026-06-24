from typing import Any


def _normalize_skill_ids(values: object) -> list[str]:
    raw_values = values if isinstance(values, list) else [values]
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        text = str(raw_value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(text)
    return normalized


def active_skill_ids_for_state(state: Any) -> list[str]:
    active_ids = _normalize_skill_ids(getattr(state, "active_skill_ids", []) or [])
    if active_ids:
        return active_ids
    return _normalize_skill_ids(
        [
            str(getattr(state, "active_skill_id", "") or "").strip(),
            *list(getattr(state, "resolved_skill_ids", []) or []),
        ]
    )


def _canonical_active_skill_id(state: Any, skill_id: str) -> str | None:
    normalized = str(skill_id or "").strip()
    if not normalized:
        return None
    for active_skill_id in active_skill_ids_for_state(state):
        if active_skill_id.lower() == normalized.lower():
            return active_skill_id
    return None


def _primary_active_skill_id(state: Any) -> str | None:
    active_ids = active_skill_ids_for_state(state)
    return active_ids[0] if active_ids else None


def _version_hash_for_skill(state: Any, skill_id: str) -> str | None:
    versions = getattr(state, "resolved_skill_versions", {}) or {}
    if not isinstance(versions, dict):
        return None
    for key, value in versions.items():
        if str(key or "").strip().lower() == skill_id.lower():
            text = str(value or "").strip()
            return text or None
    return None


def _bound_skill_from_sub_intents(state: Any, command: Any) -> str | None:
    command_intent_ids = [
        str(item or "").strip()
        for item in list(getattr(command, "sub_intent_ids", []) or [])
        if str(item or "").strip()
    ]
    if not command_intent_ids:
        return None
    bound_ids: list[str] = []
    for item in list(getattr(state, "intent_execution_states", []) or []):
        intent_id = str(getattr(item, "intent_id", "") or "").strip()
        if intent_id not in command_intent_ids:
            continue
        canonical = _canonical_active_skill_id(
            state, str(getattr(item, "skill_id", "") or "").strip()
        )
        if canonical and canonical not in bound_ids:
            bound_ids.append(canonical)
    return bound_ids[0] if len(bound_ids) == 1 else None


def resolve_skill_id_for_command(state: Any, command: Any) -> str | None:
    explicit_skill_id = str(getattr(command, "skill_id", "") or "").strip()
    if explicit_skill_id:
        canonical = _canonical_active_skill_id(state, explicit_skill_id)
        return canonical or _primary_active_skill_id(state)
    return _bound_skill_from_sub_intents(state, command) or _primary_active_skill_id(
        state
    )


def activate_skill_for_command(state: Any, command: Any) -> str | None:
    skill_id = resolve_skill_id_for_command(state, command)
    if not skill_id:
        state.active_skill_id = None
        state.active_skill_version_hash = None
        return None
    state.active_skill_id = skill_id
    state.active_skill_version_hash = _version_hash_for_skill(state, skill_id)
    return skill_id


__all__ = [
    "activate_skill_for_command",
    "active_skill_ids_for_state",
    "resolve_skill_id_for_command",
]
