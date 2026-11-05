"""Lossless wire encoding of historical condition evidence."""

import json
from typing import Any

from openminion.modules.brain.schemas.task_context import TaskConditionEvidence


def _encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def history_payload(
    history: dict[str, list[TaskConditionEvidence]],
) -> dict[str, Any]:
    """Keep every event and its order; pool exact repeats only when smaller."""
    result: dict[str, Any] = {}
    for key, items in history.items():
        events = [
            item.model_dump(exclude={"source"}, exclude_none=True) for item in items
        ]
        evidence: list[dict[str, Any]] = []
        positions: dict[tuple[str, str | None], int] = {}
        sequence = []
        for item, event in zip(items, events, strict=True):
            identity = (item.quote, item.question_quote)
            if identity not in positions:
                positions[identity] = len(evidence)
                evidence.append(event)
            sequence.append(positions[identity])
        pooled = {"evidence": evidence, "sequence": sequence}
        result[key] = (
            pooled if _encoded_size(pooled) < _encoded_size(events) else events
        )
    return result


def history_fields(
    history: dict[str, list[TaskConditionEvidence]],
) -> dict[str, Any]:
    if not history:
        return {}
    payload = history_payload(history)
    rule = (
        "Older evidence only for resolving references; latest explicit conditions "
        "supersede prior values."
    )
    if any(isinstance(value, dict) for value in payload.values()):
        rule += (
            " For pooled histories, sequence indexes evidence from zero, "
            "in chronological order (oldest first)."
        )
    return {"condition_history": payload, "history_rule": rule}
