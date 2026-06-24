from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskShapeRecurrenceWindow(BaseModel):
    """Operator-declared evidence window for recurring-shape projection."""

    model_config = ConfigDict(extra="forbid")

    time_range: str = ""
    agent_id: str = ""
    min_recurrence_threshold: int = Field(default=1, ge=1)


class RecurringTaskShape(BaseModel):
    """Typed recurring-task-shape evidence."""

    model_config = ConfigDict(extra="forbid")

    task_shape_ref: str
    strategy_id: str
    capability_category: str
    intent_category: str
    recurrence_count: int = Field(default=0, ge=0)
    performance_entry_refs: list[str] = Field(default_factory=list)
    failure_pattern_refs: list[str] = Field(default_factory=list)
    knowledge_record_refs: list[str] = Field(default_factory=list)
    evidence_window: dict[str, Any] = Field(default_factory=dict)


def _rows_from_readout(readout: Any, key: str) -> list[Any]:
    if readout is None:
        return []
    if isinstance(readout, Mapping):
        value = readout.get(key)
        return list(value) if isinstance(value, list) else []
    value = getattr(readout, key, None)
    return list(value) if isinstance(value, list) else []


def _entry_field(entry: Any, field: str) -> Any:
    if isinstance(entry, Mapping):
        return entry.get(field)
    return getattr(entry, field, None)


def _failure_pattern_refs(
    failure_readout: Any,
    *,
    min_recurrence_threshold: int,
) -> list[str]:
    refs: list[str] = []
    for row in _rows_from_readout(failure_readout, "rows"):
        seam_id = str(_entry_field(row, "seam_id") or "").strip()
        reason_code = str(_entry_field(row, "reason_code") or "").strip()
        recurrence_count = int(_entry_field(row, "recurrence_count") or 0)
        if not seam_id or not reason_code:
            continue
        if recurrence_count < min_recurrence_threshold:
            continue
        refs.append(f"failure:{seam_id}|{reason_code}")
    return sorted(set(refs))


def _knowledge_record_refs(knowledge_readout: Any) -> list[str]:
    refs: list[str] = []
    for bucket_name in ("rows", "records", "candidates", "entries"):
        for row in _rows_from_readout(knowledge_readout, bucket_name):
            for field in (
                "record_id",
                "candidate_ref",
                "consolidated_record_id",
                "proposal_ref",
                "task_shape_ref",
                "proposed_signature",
            ):
                value = str(_entry_field(row, field) or "").strip()
                if value:
                    refs.append(f"knowledge:{value}")
                    break
    return sorted(set(refs))


def _shape_parts(subject_id: str) -> tuple[str, str, str] | None:
    parts = [part.strip() for part in str(subject_id or "").split("|")]
    if len(parts) != 3 or any(not part for part in parts):
        return None
    return parts[0], parts[1], parts[2]


def project_recurring_task_shapes(
    performance_readout: Any,
    failure_readout: Any,
    knowledge_readout: Any,
    *,
    window: TaskShapeRecurrenceWindow | Mapping[str, Any],
) -> list[RecurringTaskShape]:
    """Project typed recurring task-shapes from aggregate readouts."""

    recurrence_window = (
        window
        if isinstance(window, TaskShapeRecurrenceWindow)
        else TaskShapeRecurrenceWindow.model_validate(window)
    )
    failure_refs = _failure_pattern_refs(
        failure_readout,
        min_recurrence_threshold=recurrence_window.min_recurrence_threshold,
    )
    knowledge_refs = _knowledge_record_refs(knowledge_readout)
    shapes: list[RecurringTaskShape] = []

    for entry in _rows_from_readout(performance_readout, "entries"):
        subject_kind = str(_entry_field(entry, "subject_kind") or "").strip()
        if subject_kind != "strategy":
            continue
        subject_id = str(_entry_field(entry, "subject_id") or "").strip()
        parts = _shape_parts(subject_id)
        if parts is None:
            continue
        recurrence_count = int(_entry_field(entry, "success_count") or 0)
        recurrence_count += int(_entry_field(entry, "failure_count") or 0)
        recurrence_count += int(_entry_field(entry, "other_count") or 0)
        if recurrence_count < recurrence_window.min_recurrence_threshold:
            continue
        strategy_id, capability_category, intent_category = parts
        shapes.append(
            RecurringTaskShape(
                task_shape_ref=f"task_shape:{subject_id}",
                strategy_id=strategy_id,
                capability_category=capability_category,
                intent_category=intent_category,
                recurrence_count=recurrence_count,
                performance_entry_refs=[f"performance:{subject_id}"],
                failure_pattern_refs=list(failure_refs),
                knowledge_record_refs=list(knowledge_refs),
                evidence_window=recurrence_window.model_dump(mode="json"),
            )
        )

    shapes.sort(key=lambda item: (-item.recurrence_count, item.task_shape_ref))
    return shapes
