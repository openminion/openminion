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
    _rank_post_completion_critique_cards,
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


def _critique_card(
    record_id: str,
    *,
    intent_id: str,
    route_chosen: str,
    sub_intents: list[str],
    summary: str,
    created_at: str = "2026-05-19T00:00:00+00:00",
) -> MemoryCard:
    return MemoryCard(
        record_id=record_id,
        record_type="post_completion_critique",
        text="post_completion_critique_ref",
        meta={
            "intent_id": intent_id,
            "route_chosen": route_chosen,
            "sub_intents": list(sub_intents),
            "summary": summary,
            "lessons": ["Keep the structure explicit."],
            "created_at": created_at,
        },
    )


def _segments_with_cards(
    *,
    memory_cards: list[MemoryCard],
    live_state_overlay: dict[str, Any],
) -> list[Any]:
    request = BuildPackRequest(
        session_id="s-pccm-1",
        agent_id="agent-pccm",
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
            session_id="s-pccm-1",
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


def test_post_completion_critique_ranking_uses_structural_intent_match() -> None:
    request = BuildPackRequest(
        session_id="s-pccm-rank",
        agent_id="agent-pccm",
        purpose="decide",
        mode_name="act",
        query="old lexical words should not matter",
        live_state_overlay={
            "post_completion_critique_intent_ids": ["intent-weather"],
            "post_completion_critique_sub_intents": ["intent-weather"],
            "post_completion_critique_route": "act",
        },
    )
    lexical_only = _critique_card(
        "critique-lexical",
        intent_id="intent-other",
        route_chosen="respond",
        sub_intents=["intent-other"],
        summary="old lexical words should not matter",
    )
    structural = _critique_card(
        "critique-structural",
        intent_id="intent-weather",
        route_chosen="act",
        sub_intents=["intent-weather"],
        summary="Different prose entirely.",
        created_at="2026-05-19T00:00:01+00:00",
    )

    ranked = _rank_post_completion_critique_cards(
        [lexical_only, structural],
        request=request,
    )

    assert [item.record_id for item in ranked] == [
        "critique-structural",
        "critique-lexical",
    ]


def test_decide_context_surfaces_post_completion_critique_bucket() -> None:
    segments = _segments_with_cards(
        live_state_overlay={
            "post_completion_critique_intent_ids": ["intent-weather"],
            "post_completion_critique_sub_intents": ["intent-weather"],
            "post_completion_critique_route": "act",
            "post_completion_critique_cards": [
                {
                    "record_id": "critique-1",
                    "record_type": "post_completion_critique",
                    "text": "post_completion_critique_ref",
                    "meta": {
                        "intent_id": "intent-weather",
                        "route_chosen": "act",
                        "sub_intents": ["intent-weather"],
                        "summary": "Validate required inputs before the tool call.",
                        "lessons": ["Ask for the city before searching."],
                        "next_time_action": "Request the missing input first.",
                        "created_at": "2026-05-19T00:00:01+00:00",
                    },
                }
            ],
        },
        memory_cards=[],
    )

    critique_segment = next(
        seg for seg in segments if seg.id == "retrieval:post_completion_critiques"
    )
    assert critique_segment.bucket == "retrieval"
    assert critique_segment.refs == ["critique-1"]
    assert "[POST-COMPLETION CRITIQUES]" in critique_segment.content
    assert "intent_id=intent-weather" in critique_segment.content
    assert "summary=Validate required inputs before the tool call." in (
        critique_segment.content
    )
