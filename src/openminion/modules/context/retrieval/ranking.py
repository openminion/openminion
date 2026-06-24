"""Ranking and filtering helpers for retrieval memory cards."""

from typing import Any

from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME

from .fields import (
    _structural_values,
    request_decision_match_fields as _request_decision_match_fields,
    request_improvement_note_match_fields as _request_improvement_note_match_fields,
    request_post_completion_critique_match_fields as _request_post_completion_critique_match_fields,
    request_strategy_outcome_match_fields as _request_strategy_outcome_match_fields,
)
from ..schemas import BuildPackRequest, MemoryCard


def _decision_match_score(card: MemoryCard, request_fields: dict[str, Any]) -> int:
    meta = dict(card.meta or {})
    score = 0
    reason_code = str(meta.get("reason_code") or "").strip().lower()
    if reason_code and reason_code == request_fields.get("reason_code"):
        score += 1
    sub_intents = _structural_values(meta.get("sub_intents"))
    if sub_intents and sub_intents.intersection(
        request_fields.get("sub_intents", set())
    ):
        score += 1
    for meta_field in ("act_profile", "execution_target_kind", "target_agent_id"):
        value = str(meta.get(meta_field) or "").strip().lower()
        if value and value == request_fields.get(meta_field):
            score += 1
    return score


def _card_meta_timestamp(card: MemoryCard, key: str) -> str:
    return str(dict(card.meta or {}).get(key) or "").strip()


def _rank_decision_memory_cards(
    cards: list[MemoryCard],
    *,
    request: BuildPackRequest,
) -> list[MemoryCard]:
    request_fields = _request_decision_match_fields(request)
    return sorted(
        cards,
        key=lambda card: (
            _decision_match_score(card, request_fields),
            _card_meta_timestamp(card, "created_at"),
            str(card.record_id or ""),
        ),
        reverse=True,
    )


def _improvement_note_match_score(
    card: MemoryCard,
    request_fields: dict[str, Any],
) -> tuple[int, int]:
    meta = dict(card.meta or {})
    tool_tags = {
        f"tool:{str(item).strip().lower()}"
        for item in list(meta.get("tool_slugs") or [])
        if str(item).strip()
    }
    error_tags = {
        f"error:{str(item).strip().lower()}"
        for item in list(meta.get("error_slugs") or [])
        if str(item).strip()
    }
    return (
        len(tool_tags.intersection(request_fields.get("tool_tags", set()))),
        len(error_tags.intersection(request_fields.get("error_tags", set()))),
    )


def _rank_improvement_note_cards(
    cards: list[MemoryCard],
    *,
    request: BuildPackRequest,
) -> list[MemoryCard]:
    request_fields = _request_improvement_note_match_fields(request)
    ranked = list(cards)
    ranked.sort(key=lambda card: str(card.record_id or ""))
    ranked.sort(key=lambda card: _card_meta_timestamp(card, "updated_at"), reverse=True)
    ranked.sort(
        key=lambda card: _improvement_note_match_score(card, request_fields)[1],
        reverse=True,
    )
    ranked.sort(
        key=lambda card: _improvement_note_match_score(card, request_fields)[0],
        reverse=True,
    )
    return ranked


def _strategy_outcome_match_score(
    card: MemoryCard,
    request_fields: dict[str, Any],
) -> int:
    meta = dict(card.meta or {})
    score = 0
    for meta_field in ("strategy_id", "capability_category", "intent_category"):
        value = str(meta.get(meta_field) or "").strip().lower()
        if value and value == request_fields.get(meta_field):
            score += 1
    return score


def _rank_strategy_outcome_cards(
    cards: list[MemoryCard],
    *,
    request: BuildPackRequest,
) -> list[MemoryCard]:
    request_fields = _request_strategy_outcome_match_fields(request)
    ranked = list(cards)
    ranked.sort(key=lambda card: str(card.record_id or ""))
    ranked.sort(key=lambda card: _card_meta_timestamp(card, "created_at"), reverse=True)
    ranked.sort(
        key=lambda card: _strategy_outcome_match_score(card, request_fields),
        reverse=True,
    )
    return ranked


def _post_completion_critique_match_score(
    card: MemoryCard,
    request_fields: dict[str, Any],
) -> int:
    meta = dict(card.meta or {})
    score = 0
    intent_id = str(meta.get("intent_id") or "").strip().lower()
    if intent_id and intent_id in request_fields.get("intent_ids", set()):
        score += 2
    sub_intents = _structural_values(meta.get("sub_intents"))
    if sub_intents and sub_intents.intersection(
        request_fields.get("sub_intents", set())
    ):
        score += 1
    route_chosen = str(meta.get("route_chosen") or "").strip().lower()
    if route_chosen and route_chosen == request_fields.get("route_chosen"):
        score += 1
    return score


def _rank_post_completion_critique_cards(
    cards: list[MemoryCard],
    *,
    request: BuildPackRequest,
) -> list[MemoryCard]:
    request_fields = _request_post_completion_critique_match_fields(request)
    ranked = list(cards)
    ranked.sort(key=lambda card: str(card.record_id or ""))
    ranked.sort(key=lambda card: _card_meta_timestamp(card, "created_at"), reverse=True)
    ranked.sort(
        key=lambda card: _post_completion_critique_match_score(card, request_fields),
        reverse=True,
    )
    return ranked


def _retrieval_item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _retrieval_item_tags(item: Any) -> set[str]:
    return {
        str(item or "").strip().lower()
        for item in list(_retrieval_item_value(item, "tags", []) or [])
        if str(item or "").strip()
    }


def _retrieval_item_meta(item: Any) -> dict[str, Any]:
    raw = _retrieval_item_value(item, "meta", {}) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _is_operational_tool_failure_item(item: Any) -> bool:
    tags = _retrieval_item_tags(item)
    meta = _retrieval_item_meta(item)
    if "tool_failure" in tags or bool(meta.get("source_negative_outcome")):
        return True
    source_kind = str(meta.get("source_kind") or "").strip().lower()
    outcome_status = str(meta.get(STATE_KEY_SOURCE_OUTCOME) or "").strip().lower()
    if source_kind == "tool_outcome" and outcome_status not in {
        "",
        "ok",
        "success",
        "succeeded",
    }:
        return True
    return "tool_outcome" in tags and any(
        tag in tags for tag in {"outcome:error", "outcome:failed", "outcome:failure"}
    )
