from __future__ import annotations

from typing import Any

from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    MemoryCard,
    SessionSlice,
    bucket_caps_for,
)
from openminion.modules.context.segment import (
    _rank_strategy_outcome_cards,
    assemble_segments,
)


class _Prefix:
    def build(self, **kwargs: Any) -> str:
        del kwargs
        return "identity"


class _PluginRegistry:
    retriever_names: list[str] = []


def _fit(text: str, cap_tokens: int) -> tuple[str, bool]:
    max_chars = max(0, int(cap_tokens or 0) * 10)
    if max_chars and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _tokens(text: str) -> int:
    return max(1, len(str(text or "").split())) if str(text or "").strip() else 0


def _project(_: dict[str, Any] | None) -> tuple[Any | None, dict[str, int]]:
    return None, {"raw_chars": 0, "projected_chars": 0, "chars_saved": 0}


def _strategy_outcome_card(
    record_id: str,
    *,
    strategy_id: str,
    capability_category: str,
    intent_category: str,
    outcome_status: str = "success",
    created_at: str = "2026-05-08T00:00:00+00:00",
) -> MemoryCard:
    return MemoryCard(
        record_id=record_id,
        record_type="strategy_outcome",
        text="strategy_outcome_ref",
        meta={
            "strategy_id": strategy_id,
            "capability_category": capability_category,
            "intent_category": intent_category,
            "outcome_status": outcome_status,
            "created_at": created_at,
        },
    )


def _segments_with_cards(
    *,
    memory_cards: list[MemoryCard],
    live_state_overlay: dict[str, Any],
) -> list[Any]:
    request = BuildPackRequest(
        session_id="s-socr-1",
        agent_id="agent-socr",
        purpose="decide",
        mode_name="act",
        query="continue",
        live_state_overlay=live_state_overlay,
    )
    budgets = ContextBudgets(
        total_max_tokens=1200,
        identity_tokens=40,
        summary_tokens=40,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        trailer_feedback_tokens=0,
        recent_turn_tokens=100,
        facts_tokens=40,
        memory_tokens=120,
        skills_tokens=0,
        artifact_tokens=0,
        instructions_tokens=80,
    )
    segments, _, _ = assemble_segments(
        request=request,
        constraints=BuildConstraints(),
        prompt_tool_schemas=[],
        budgets=budgets,
        bucket_caps=bucket_caps_for(budgets),
        identity_text="identity",
        session_slice=SessionSlice(
            session_id="s-socr-1",
            slice_version="slice-1",
            summary_short="",
            recent_turns=[],
            active_state=None,
        ),
        fact_records=[],
        memory_cards=memory_cards,
        recalled_memory_cards=[],
        recent_session_artifact_refs=[],
        procedure=None,
        skill_snippet_text=None,
        artifact_digests=[],
        seed_text=None,
        rolling_enabled=True,
        compression_enabled=False,
        prefix_builder=_Prefix(),
        compressctl=None,
        rlmctl=None,
        vectorctl=None,
        plugin_registry=_PluginRegistry(),
        run_plugin_evidence_pipeline=lambda **_: [],
        project_active_state_to_prompt_view=_project,
        build_clarify_digest=lambda _: "",
        fit_to_budget=_fit,
        estimate_tokens=_tokens,
    )
    return segments


def test_strategy_outcome_ranking_uses_structural_fields() -> None:
    request = BuildPackRequest(
        session_id="s-socr-rank",
        agent_id="agent-socr",
        purpose="decide",
        mode_name="act",
        query="old lexical words should not matter",
        live_state_overlay={
            "strategy_outcome_strategy_id": "research",
            "strategy_outcome_capability_category": "live_information",
            "strategy_outcome_intent_category": "latest_news",
        },
    )
    lexical_only = _strategy_outcome_card(
        "outcome-lexical",
        strategy_id="coding",
        capability_category="artifact_change",
        intent_category="patch_bug",
    )
    structural = _strategy_outcome_card(
        "outcome-structural",
        strategy_id="research",
        capability_category="live_information",
        intent_category="latest_news",
    )

    ranked = _rank_strategy_outcome_cards([lexical_only, structural], request=request)

    assert [item.record_id for item in ranked] == [
        "outcome-structural",
        "outcome-lexical",
    ]


def test_decide_context_surfaces_strategy_outcome_bucket() -> None:
    segments = _segments_with_cards(
        live_state_overlay={
            "strategy_outcome_strategy_id": "research",
            "strategy_outcome_capability_category": "live_information",
            "strategy_outcome_intent_category": "latest_news",
            "strategy_outcome_cards": [
                {
                    "record_id": "outcome-1",
                    "record_type": "strategy_outcome",
                    "text": "strategy_outcome_ref",
                    "meta": {
                        "strategy_id": "research",
                        "capability_category": "live_information",
                        "intent_category": "latest_news",
                        "outcome_status": "success",
                        "created_at": "2026-05-08T00:00:01+00:00",
                    },
                }
            ],
        },
        memory_cards=[],
    )

    strategy_segment = next(
        seg for seg in segments if seg.id == "retrieval:strategy_outcomes"
    )
    assert strategy_segment.bucket == "retrieval"
    assert strategy_segment.refs == ["outcome-1"]
    assert "[STRATEGY OUTCOMES]" in strategy_segment.content
    assert "strategy_id=research" in strategy_segment.content
    assert "capability_category=live_information" in strategy_segment.content
    assert "intent_category=latest_news" in strategy_segment.content
