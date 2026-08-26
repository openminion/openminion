"""Capture-assurance projection over the existing durable memory event stream."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


CaptureDisposition = Literal[
    "pending",
    "processed",
    "succeeded_no_output",
    "rejected",
    "failed_terminal",
]


@dataclass(frozen=True)
class CaptureProcessingState:
    evidence_id: str
    disposition: CaptureDisposition
    reason: str
    patch_id: str
    event_id: str
    updated_at: str


@dataclass(frozen=True)
class CaptureProcessingSummary:
    pending: int
    processed: int
    succeeded_no_output: int
    rejected: int
    failed_terminal: int
    oldest_pending_at: str


def _value(event: Any, name: str, default: Any = "") -> Any:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _payload(event: Any) -> dict[str, Any]:
    value = _value(event, "payload", {})
    return dict(value) if isinstance(value, Mapping) else {}


def project_capture_processing(
    events: list[Any],
) -> dict[str, CaptureProcessingState]:
    """Return the latest typed capture disposition for each durable turn."""

    projected: dict[str, CaptureProcessingState] = {}
    for event in events:
        event_type = str(
            _value(event, "event_type", "") or _value(event, "type", "") or ""
        ).strip()
        if event_type not in {
            "memory.write.started",
            "memory.write.completed",
            "memory.write.rejected",
            "memory.write.failed",
        }:
            continue
        payload = _payload(event)
        evidence_id = str(payload.get("capture_evidence_id", "") or "").strip()
        if not evidence_id:
            continue
        if event_type == "memory.write.started":
            disposition: CaptureDisposition = "pending"
        elif event_type == "memory.write.completed":
            disposition = (
                "processed"
                if str(payload.get("changed", "false")).lower() == "true"
                else "succeeded_no_output"
            )
        elif event_type == "memory.write.rejected":
            disposition = "rejected"
        else:
            disposition = "failed_terminal"
        projected[evidence_id] = CaptureProcessingState(
            evidence_id=evidence_id,
            disposition=disposition,
            reason=str(payload.get("capture_reason", "") or ""),
            patch_id=str(payload.get("patch_id", "") or ""),
            event_id=str(_value(event, "event_id", "") or _value(event, "id", "")),
            updated_at=str(
                _value(event, "timestamp", "")
                or _value(event, "created_at", "")
                or _value(event, "ts", "")
                or ""
            ),
        )
    return projected


def summarize_capture_processing(
    states: dict[str, CaptureProcessingState],
) -> CaptureProcessingSummary:
    """Return content-free capture health facts for operator surfaces."""

    counts = {
        disposition: sum(state.disposition == disposition for state in states.values())
        for disposition in (
            "pending",
            "processed",
            "succeeded_no_output",
            "rejected",
            "failed_terminal",
        )
    }
    pending_times = sorted(
        state.updated_at
        for state in states.values()
        if state.disposition == "pending" and state.updated_at
    )
    return CaptureProcessingSummary(
        pending=counts["pending"],
        processed=counts["processed"],
        succeeded_no_output=counts["succeeded_no_output"],
        rejected=counts["rejected"],
        failed_terminal=counts["failed_terminal"],
        oldest_pending_at=pending_times[0] if pending_times else "",
    )


__all__ = [
    "CaptureProcessingState",
    "CaptureProcessingSummary",
    "project_capture_processing",
    "summarize_capture_processing",
]
