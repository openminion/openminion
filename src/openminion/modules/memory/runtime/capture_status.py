"""Capture-assurance projection over the existing durable memory event stream."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping


CaptureDisposition = Literal[
    "pending",
    "processed",
    "succeeded_no_output",
    "rejected",
    "failed_terminal",
]
RecallHealth = Literal["healthy", "disabled", "unsupported", "degraded"]

_CAPTURE_TERMINAL_DISPOSITIONS = frozenset(
    {"processed", "succeeded_no_output", "rejected", "failed_terminal"}
)
_RECALL_CAPABILITIES = (
    "keyword",
    "graph",
    "recency",
    "trust",
    "vector",
    "rerank",
)


@dataclass(frozen=True)
class CaptureProcessingState:
    evidence_id: str
    disposition: CaptureDisposition
    reason: str
    patch_id: str
    event_id: str
    updated_at: str
    eligible: bool = True
    integrity_error: bool = False


@dataclass(frozen=True)
class CaptureProcessingSummary:
    pending: int
    processed: int
    succeeded_no_output: int
    rejected: int
    failed_terminal: int
    oldest_pending_at: str
    eligible: int = 0
    terminal: int = 0
    integrity_errors: int = 0


@dataclass(frozen=True)
class RecallProcessingSummary:
    health: RecallHealth
    mode: str
    capabilities: tuple[str, ...]
    score_domain: str
    selected_memory: int = 0
    selected_knowledge: int = 0
    omission_reasons: tuple[tuple[str, int], ...] = ()


def _value(event: Any, name: str, default: Any = "") -> Any:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _payload(event: Any) -> dict[str, Any]:
    value = _value(event, "payload", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _event_type(event: Any) -> str:
    return str(
        _value(event, "event_type", "") or _value(event, "type", "") or ""
    ).strip()


def _event_time(event: Any) -> str:
    return str(
        _value(event, "timestamp", "")
        or _value(event, "created_at", "")
        or _value(event, "ts", "")
        or ""
    )


def _capture_event_state(
    event: Any,
) -> tuple[str, CaptureDisposition, bool, str, str] | None:
    event_type = _event_type(event)
    payload = _payload(event)
    if event_type == "turn.outcome":
        capture_id = str(payload.get("capture_id", "") or "").strip()
        if not capture_id:
            return None
        state = str(payload.get("capture_state", "") or "").strip()
        if state == "pending":
            return capture_id, "pending", True, "", ""
        if state == "excluded":
            return (
                capture_id,
                "rejected",
                False,
                str(payload.get("capture_reason", "") or ""),
                "",
            )
        return None
    if event_type == "memory.capture.result":
        capture_id = str(payload.get("capture_id", "") or "").strip()
        raw_disposition = str(payload.get("disposition", "") or "").strip()
        if not capture_id or raw_disposition == "pending":
            return None
        disposition: CaptureDisposition
        if raw_disposition in {"succeeded", "processed"}:
            disposition = "processed"
        elif raw_disposition == "succeeded_no_output":
            disposition = "succeeded_no_output"
        elif raw_disposition == "rejected":
            disposition = "rejected"
        else:
            disposition = "failed_terminal"
        return (
            capture_id,
            disposition,
            True,
            str(payload.get("reason_code", "") or ""),
            str(payload.get("result_hash", "") or ""),
        )
    if event_type not in {
        "memory.write.started",
        "memory.write.completed",
        "memory.write.rejected",
        "memory.write.failed",
    }:
        return None
    evidence_id = str(payload.get("capture_evidence_id", "") or "").strip()
    if not evidence_id:
        return None
    if event_type == "memory.write.started":
        disposition = "pending"
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
    reason = str(payload.get("capture_reason", "") or "")
    eligible = reason not in {"memory_disabled", "backend_none"}
    return (
        evidence_id,
        disposition,
        eligible,
        reason,
        str(payload.get("patch_id", "") or ""),
    )


def project_capture_processing(
    events: list[Any],
) -> dict[str, CaptureProcessingState]:
    """Return the latest typed capture disposition for each durable turn."""

    projected: dict[str, CaptureProcessingState] = {}
    for event in events:
        event_state = _capture_event_state(event)
        if event_state is None:
            continue
        evidence_id, disposition, eligible, reason, patch_id = event_state
        prior = projected.get(evidence_id)
        terminal_conflict = bool(
            prior
            and prior.disposition in _CAPTURE_TERMINAL_DISPOSITIONS
            and disposition in _CAPTURE_TERMINAL_DISPOSITIONS
            and (
                prior.disposition != disposition
                or (prior.patch_id and patch_id and prior.patch_id != patch_id)
            )
        )
        if prior and prior.disposition in _CAPTURE_TERMINAL_DISPOSITIONS:
            if disposition == "pending" or terminal_conflict:
                if terminal_conflict:
                    projected[evidence_id] = replace(prior, integrity_error=True)
                continue
        projected[evidence_id] = CaptureProcessingState(
            evidence_id=evidence_id,
            disposition=disposition,
            reason=reason,
            patch_id=patch_id,
            event_id=str(_value(event, "event_id", "") or _value(event, "id", "")),
            updated_at=_event_time(event),
            eligible=eligible or bool(prior and prior.eligible),
            integrity_error=terminal_conflict,
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
        eligible=sum(state.eligible for state in states.values()),
        terminal=sum(
            state.disposition in _CAPTURE_TERMINAL_DISPOSITIONS
            for state in states.values()
        ),
        integrity_errors=sum(state.integrity_error for state in states.values()),
    )


def _non_negative_int(payload: Mapping[str, Any], key: str) -> int:
    try:
        return max(0, int(payload.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _normalized_capabilities(value: Any) -> tuple[str, ...]:
    raw = value.split(",") if isinstance(value, str) else list(value or ())
    present = {str(item or "").strip().lower() for item in raw}
    return tuple(name for name in _RECALL_CAPABILITIES if name in present)


def _score_domain(*, mode: str, capabilities: tuple[str, ...]) -> str:
    if "vector" in capabilities:
        return "hybrid-semantic-v1"
    if mode in {"shadow", "sophiagraph"}:
        return "structured-retrieval-v1"
    return "legacy-retrieval-v1"


def _add_omission(
    counts: dict[str, int],
    *,
    reason: str,
    count: int,
) -> None:
    if count <= 0:
        return
    counts[reason] = counts.get(reason, 0) + count


def _project_recall_events(
    events: list[Any],
    *,
    mode: str,
    capabilities: tuple[str, ...],
) -> tuple[str, tuple[str, ...], int, int, dict[str, int], bool, bool]:
    selected_memory = 0
    selected_knowledge = 0
    omissions: dict[str, int] = {}
    memory_degraded = False
    knowledge_degraded = False
    for event in events:
        event_type = _event_type(event)
        payload = _payload(event)
        if event_type == "memory.context.failed":
            memory_degraded = True
        if event_type in {"memory.retrieval.built", "memory.recall.status"}:
            for reason in ("relevance", "duplicate", "budget"):
                omissions.pop(f"memory:{reason}", None)
            status = str(payload.get("status", "") or "").strip().lower()
            memory_degraded = status == "degraded"
            mode = str(payload.get("memory_recall_mode", mode) or mode).strip().lower()
            capabilities = _normalized_capabilities(
                payload.get("memory_recall_capabilities", capabilities)
            )
            selected_memory = _non_negative_int(
                payload, "memory_envelope_included_items"
            )
            for reason, key in (
                ("relevance", "memory_recall_threshold_drops"),
                ("duplicate", "evidence_duplicate_omissions"),
                ("budget", "evidence_budget_omissions"),
            ):
                _add_omission(
                    omissions,
                    reason=f"memory:{reason}",
                    count=_non_negative_int(payload, key),
                )
        if event_type in {
            "knowledge_graph.query.completed",
            "knowledge_graph.query.degraded",
            "knowledge_graph.query.failed",
        }:
            omissions.pop("knowledge:relevance", None)
            knowledge_degraded = event_type != "knowledge_graph.query.completed"
            selected_knowledge = _non_negative_int(payload, "knowledge_graph_results")
            _add_omission(
                omissions,
                reason="knowledge:relevance",
                count=_non_negative_int(payload, "knowledge_graph_omitted"),
            )
    merged: dict[str, int] = {}
    for qualified_reason, count in omissions.items():
        reason = qualified_reason.partition(":")[2]
        merged[reason] = merged.get(reason, 0) + count
    return (
        mode,
        capabilities,
        selected_memory,
        selected_knowledge,
        merged,
        memory_degraded,
        knowledge_degraded,
    )


def summarize_recall_processing(
    events: list[Any],
    *,
    enabled: bool,
    mode: str,
    capabilities: tuple[str, ...] = (),
    supported: bool = True,
) -> RecallProcessingSummary:
    """Return the latest content-free recall facts for operator surfaces."""

    normalized_mode = str(mode or "legacy").strip().lower()
    normalized_capabilities = _normalized_capabilities(capabilities)
    (
        normalized_mode,
        normalized_capabilities,
        selected_memory,
        selected_knowledge,
        omissions,
        memory_degraded,
        knowledge_degraded,
    ) = _project_recall_events(
        events,
        mode=normalized_mode,
        capabilities=normalized_capabilities,
    )
    health: RecallHealth
    if not enabled:
        health = "disabled"
    elif not supported:
        health = "unsupported"
    elif memory_degraded or knowledge_degraded:
        health = "degraded"
    else:
        health = "healthy"
    return RecallProcessingSummary(
        health=health,
        mode=(
            normalized_mode
            if normalized_mode in {"legacy", "shadow", "sophiagraph"}
            else "unsupported"
        ),
        capabilities=normalized_capabilities,
        score_domain=(
            "unavailable"
            if health in {"disabled", "unsupported"}
            else _score_domain(
                mode=normalized_mode,
                capabilities=normalized_capabilities,
            )
        ),
        selected_memory=selected_memory,
        selected_knowledge=selected_knowledge,
        omission_reasons=tuple(sorted(omissions.items())),
    )


__all__ = [
    "CaptureProcessingState",
    "CaptureProcessingSummary",
    "RecallProcessingSummary",
    "project_capture_processing",
    "summarize_recall_processing",
    "summarize_capture_processing",
]
