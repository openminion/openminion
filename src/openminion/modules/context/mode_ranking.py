"""Shared mode normalization and memory-card ranking for context assembly."""

from .schemas import MemoryCard

_MODE_RESPOND = "respond"
_MODE_ACT = "act"
_MODE_PLAN = "plan"
_SUPPORTED_MODE_NAMES = {_MODE_RESPOND, _MODE_ACT, _MODE_PLAN}


def normalize_mode_name(mode_name: str | None) -> str | None:
    normalized = str(mode_name or "").strip().lower()
    if normalized in _SUPPORTED_MODE_NAMES:
        return normalized
    return None


def rank_memory_cards_for_mode(
    cards: list[MemoryCard],
    *,
    mode_name: str | None,
) -> list[MemoryCard]:
    normalized_mode = normalize_mode_name(mode_name)
    if normalized_mode is None:
        return list(cards)
    if normalized_mode == _MODE_PLAN:
        priority = {"procedure": 0, "task": 1, "decision": 2, "summary": 3}
    elif normalized_mode == _MODE_RESPOND:
        priority = {"fact": 0, "summary": 1, "pin": 2, "decision": 3}
    else:
        priority = {"pin": 0, "procedure": 1, "task": 2, "decision": 3}
    return sorted(
        cards,
        key=lambda item: (
            priority.get(str(item.record_type or "").strip().lower(), 99),
            -float(item.score or 0.0),
            str(item.record_id or ""),
        ),
    )
