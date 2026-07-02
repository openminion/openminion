"""Reuse helpers for learned skills."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _entry_field(entry: Any, field: str, default: Any = None) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(field, default)
    return getattr(entry, field, default)


def _unscoped(value: object) -> str:
    return str(value or "").split(":", 1)[-1]


def record_learned_skill_reuse(
    skill_runtime: Any,
    *,
    session_id: str,
    agent_id: str,
    skill_id: str,
    version_hash: str,
    used_for: str = "act",
    outcome: str = "success",
    evidence_refs: list[str] | None = None,
) -> str:
    """Record learned-skill reuse through the existing ``skill.log_run`` owner."""

    return skill_runtime.log_run(
        session_id=session_id,
        agent_id=agent_id,
        skill_id=skill_id,
        version_hash=version_hash,
        used_for=used_for,
        outcome=outcome,
        evidence_refs=evidence_refs or [],
    )


def matching_catalog_entries(shape: Any, catalog_entries: Iterable[Any]) -> list[Any]:
    """Find existing catalog entries that match a workflow shape structurally."""

    intent = _unscoped(getattr(shape, "intent_category", ""))
    capability = _unscoped(getattr(shape, "capability_category", ""))
    strategy = _unscoped(getattr(shape, "strategy_id", ""))
    matches: list[Any] = []
    for entry in catalog_entries or []:
        tags = {_unscoped(tag) for tag in _entry_field(entry, "tags", [])}
        applies_to = _entry_field(entry, "applies_to", {})
        intents = {
            _unscoped(item)
            for item in (
                applies_to.get("intents", []) if isinstance(applies_to, Mapping) else []
            )
        }
        if intent in intents and {capability, strategy}.issubset(tags):
            matches.append(entry)
    return matches


__all__ = ("matching_catalog_entries", "record_learned_skill_reuse")
