"""Memory-card text renderers for retrieval assembly."""

from collections.abc import Callable

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

from ..schemas import MemoryCard


def _render_generic_card_lines(
    *,
    heading: str,
    cards: list[MemoryCard],
    build_parts: Callable[[MemoryCard], list[str]],
) -> str:
    lines = [heading]
    lines.extend(
        f"- ({card.record_id}) " + "; ".join(build_parts(card)) for card in cards
    )
    return "\n".join(lines)


def _render_decision_memory_cards(cards: list[MemoryCard]) -> str:
    lines = ["Decision cards:"]
    for card in cards:
        meta = dict(card.meta or {})
        parts = [
            f"route={str(meta.get('route_chosen') or '').strip() or 'unknown'}",
            f"reason_code={str(meta.get('reason_code') or '').strip() or 'unknown'}",
        ]
        sub_intents = [
            str(item).strip()
            for item in list(meta.get("sub_intents") or [])
            if str(item).strip()
        ]
        if sub_intents:
            parts.append("sub_intents=" + ",".join(sub_intents[:5]))
        for label, meta_field in (
            ("act_profile", "act_profile"),
            ("execution_target", "execution_target_kind"),
            ("target_agent_id", "target_agent_id"),
            (STATE_KEY_FINALIZATION_STATUS, STATE_KEY_FINALIZATION_STATUS),
        ):
            value = str(meta.get(meta_field) or "").strip()
            if value:
                parts.append(f"{label}={value}")
        rationale = str(meta.get("rationale") or "").strip()
        if rationale:
            parts.append(f"rationale={rationale[:240].rstrip()}")
        lines.append(f"- ({card.record_id}) " + "; ".join(parts))
    return "\n".join(lines)


def _render_improvement_note_cards(cards: list[MemoryCard]) -> str:
    def _parts(card: MemoryCard) -> list[str]:
        meta = dict(card.meta or {})
        parts = [f"status={str(meta.get('status') or '').strip() or 'unknown'}"]
        for field_name in ("tool_slugs", "error_slugs"):
            values = [
                str(item).strip()
                for item in list(meta.get(field_name) or [])
                if str(item).strip()
            ]
            if values:
                parts.append(f"{field_name}=" + ",".join(values[:5]))
        occurrence_count = meta.get("occurrence_count")
        if occurrence_count is not None:
            parts.append(f"occurrence_count={int(occurrence_count)}")
        updated_at = str(meta.get("updated_at") or "").strip()
        if updated_at:
            parts.append(f"updated_at={updated_at}")
        guidance = str(meta.get("guidance") or "").strip()
        if guidance:
            parts.append(f"guidance={guidance}")
        return parts

    return _render_generic_card_lines(
        heading="Improvement notes:",
        cards=cards,
        build_parts=_parts,
    )


def _render_strategy_outcome_cards(cards: list[MemoryCard]) -> str:
    def _parts(card: MemoryCard) -> list[str]:
        meta = dict(card.meta or {})
        parts = [
            f"strategy_id={str(meta.get('strategy_id') or '').strip() or 'unknown'}",
            f"outcome_status={str(meta.get('outcome_status') or '').strip() or 'unknown'}",
        ]
        for field_name in (
            "capability_category",
            "intent_category",
            "created_at",
            "termination_reason",
        ):
            value = str(meta.get(field_name) or "").strip()
            if value:
                parts.append(f"{field_name}={value}")
        return parts

    return _render_generic_card_lines(
        heading="Strategy outcome cards:",
        cards=cards,
        build_parts=_parts,
    )


def _render_post_completion_critique_cards(cards: list[MemoryCard]) -> str:
    def _parts(card: MemoryCard) -> list[str]:
        meta = dict(card.meta or {})
        parts = [
            f"intent_id={str(meta.get('intent_id') or '').strip() or 'unknown'}",
            f"summary={str(meta.get('summary') or '').strip() or 'unknown'}",
        ]
        route_chosen = str(meta.get("route_chosen") or "").strip()
        if route_chosen:
            parts.append(f"route={route_chosen}")
        sub_intents = [
            str(item).strip()
            for item in list(meta.get("sub_intents") or [])
            if str(item).strip()
        ]
        if sub_intents:
            parts.append("sub_intents=" + ",".join(sub_intents[:5]))
        lessons = [
            str(item).strip()
            for item in list(meta.get("lessons") or [])
            if str(item).strip()
        ]
        if lessons:
            parts.append("lessons=" + " | ".join(lessons[:3]))
        next_time_action = str(meta.get("next_time_action") or "").strip()
        if next_time_action:
            parts.append(f"next_time_action={next_time_action}")
        return parts

    return _render_generic_card_lines(
        heading="Post-completion critiques:",
        cards=cards,
        build_parts=_parts,
    )
