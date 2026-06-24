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
    _rank_improvement_note_cards,
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


def _improvement_note_card(
    record_id: str,
    *,
    tool_slugs: list[str],
    error_slugs: list[str] | None = None,
    updated_at: str = "2026-05-08T00:00:00+00:00",
    guidance: str = "Use structured arguments.",
) -> MemoryCard:
    return MemoryCard(
        record_id=record_id,
        record_type="improvement_note",
        text="improvement_note_ref",
        meta={
            "status": "active",
            "tool_slugs": list(tool_slugs),
            "error_slugs": list(error_slugs or []),
            "guidance": guidance,
            "occurrence_count": 2,
            "updated_at": updated_at,
        },
    )


def _segments_with_cards(
    *,
    memory_cards: list[MemoryCard],
    live_state_overlay: dict[str, Any],
    active_state: dict[str, Any] | None = None,
) -> list[Any]:
    request = BuildPackRequest(
        session_id="s-sinr-1",
        agent_id="agent-sinr",
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
            session_id="s-sinr-1",
            slice_version="slice-1",
            summary_short="",
            recent_turns=[],
            active_state=active_state,
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


def test_improvement_note_ranking_uses_structural_tag_matches() -> None:
    request = BuildPackRequest(
        session_id="s-sinr-rank",
        agent_id="agent-sinr",
        purpose="decide",
        mode_name="act",
        query="old lexical words should not matter",
        live_state_overlay={
            "improvement_note_tool_tags": ["tool:weather-openmeteo-current"],
            "improvement_note_error_tags": ["error:missing-city"],
        },
    )
    lexical_only = _improvement_note_card(
        "note-lexical",
        tool_slugs=["file-read"],
        guidance="old lexical words should not matter",
    )
    structural = _improvement_note_card(
        "note-structural",
        tool_slugs=["weather-openmeteo-current"],
        error_slugs=["missing-city"],
        guidance="Different prose entirely.",
    )

    ranked = _rank_improvement_note_cards([lexical_only, structural], request=request)

    assert [item.record_id for item in ranked] == ["note-structural", "note-lexical"]


def test_decide_context_surfaces_improvement_note_bucket() -> None:
    segments = _segments_with_cards(
        live_state_overlay={
            "improvement_note_tool_tags": ["tool:weather-openmeteo-current"],
            "improvement_note_error_tags": ["error:missing-city"],
            "improvement_note_cards": [
                {
                    "record_id": "note-1",
                    "record_type": "improvement_note",
                    "text": "improvement_note_ref",
                    "meta": {
                        "status": "active",
                        "tool_slugs": ["weather-openmeteo-current"],
                        "error_slugs": ["missing-city"],
                        "guidance": "Validate args before retrying.",
                        "occurrence_count": 2,
                        "updated_at": "2026-05-08T00:00:01+00:00",
                    },
                }
            ],
        },
        memory_cards=[],
    )

    improvement_segment = next(
        seg for seg in segments if seg.id == "retrieval:improvement_notes"
    )
    assert improvement_segment.bucket == "retrieval"
    assert improvement_segment.refs == ["note-1"]
    assert "[IMPROVEMENT NOTES]" in improvement_segment.content
    assert "tool_slugs=weather-openmeteo-current" in improvement_segment.content
    assert "guidance=Validate args before retrying." in improvement_segment.content
