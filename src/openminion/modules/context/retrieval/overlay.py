"""Overlay-to-memory-card helpers for retrieval assembly."""

from typing import Any

from ..constants import DECISION_MEMORY_LIMIT
from ..schemas import BuildPackRequest, MemoryCard, SessionSlice


def _merged_overlay(
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
) -> dict[str, Any]:
    active_state = (
        dict(session_slice.active_state)
        if isinstance(session_slice.active_state, dict)
        else {}
    )
    return {**active_state, **dict(request.live_state_overlay or {})}


def _state_decision_memory_cards(
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
) -> list[MemoryCard]:
    overlay = _merged_overlay(request=request, session_slice=session_slice)
    refs = [
        str(item).strip()
        for item in list(overlay.get("decision_memory_refs") or [])
        if str(item).strip()
    ]
    if not refs:
        return []
    meta = {
        "route_chosen": str(overlay.get("active_mode_name") or "").strip(),
        "reason_code": str(overlay.get("decision_reason_code") or "").strip(),
        "sub_intents": [
            str(item).strip()
            for item in list(overlay.get("decision_sub_intents") or [])
            if str(item).strip()
        ],
        "rationale": str(overlay.get("decision_rationale") or "").strip(),
        "act_profile": str(overlay.get("working_act_profile") or "").strip(),
        "execution_target_kind": str(
            overlay.get("working_execution_target_kind") or ""
        ).strip(),
        "target_agent_id": str(overlay.get("delegation_target_agent_id") or "").strip(),
        "created_at": str(overlay.get("decision_context_recorded_at") or "").strip(),
    }
    return [
        MemoryCard(
            record_id=record_id,
            record_type="decision",
            text="decision_memory_ref",
            meta=dict(meta),
        )
        for record_id in refs[:DECISION_MEMORY_LIMIT]
    ]


def _cards_from_overlay(
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    overlay_key: str,
    record_type: str,
    default_text: str,
) -> list[MemoryCard]:
    overlay = _merged_overlay(request=request, session_slice=session_slice)
    cards: list[MemoryCard] = []
    for item in list(overlay.get(overlay_key) or []):
        if not isinstance(item, dict):
            continue
        meta = (
            dict(item.get("meta") or {}) if isinstance(item.get("meta"), dict) else {}
        )
        cards.append(
            MemoryCard(
                record_id=str(item.get("record_id") or "").strip(),
                record_type=record_type,
                text=str(item.get("text") or default_text).strip(),
                meta=meta,
            )
        )
    return cards


def _cards_by_record_type(
    cards: list[MemoryCard], record_type: str
) -> list[MemoryCard]:
    normalized_record_type = str(record_type or "").strip().lower()
    return [
        item
        for item in cards
        if str(item.record_type or "").strip().lower() == normalized_record_type
    ]


def _extend_unique_record_cards(
    cards: list[MemoryCard], additions: list[MemoryCard]
) -> list[MemoryCard]:
    seen_ids = {str(item.record_id or "").strip() for item in cards}
    cards.extend(
        item for item in additions if str(item.record_id or "").strip() not in seen_ids
    )
    return cards


def _low_progress_signal_payload(
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
) -> dict[str, Any]:
    payload = _merged_overlay(request=request, session_slice=session_slice).get(
        "low_progress_signal"
    )
    return dict(payload) if isinstance(payload, dict) else {}
