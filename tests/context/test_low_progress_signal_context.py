from __future__ import annotations

from typing import Any

from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    SessionSlice,
    bucket_caps_for,
)
from openminion.modules.context.segment import assemble_segments


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


def _project(
    active_state: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    return active_state, {"raw_chars": 0, "projected_chars": 0, "chars_saved": 0}


def test_decide_context_surfaces_low_progress_signal_in_retrieval_bucket() -> None:
    request = BuildPackRequest(
        session_id="s-lpsrt-1",
        agent_id="agent-lpsrt",
        purpose="decide",
        mode_name="act",
        query="continue",
        live_state_overlay={
            "low_progress_signal": {
                "iterations_without_new_typed_record": 3,
                "repeated_arg_signature_count": 2,
                "unique_tool_call_count_delta": 2,
            }
        },
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
            session_id="s-lpsrt-1",
            slice_version="slice-1",
            summary_short="",
            recent_turns=[],
            active_state={},
        ),
        fact_records=[],
        memory_cards=[],
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

    low_progress_segment = next(
        seg for seg in segments if seg.id == "retrieval:low_progress_signal"
    )
    assert low_progress_segment.bucket == "retrieval"
    assert "[LOW PROGRESS SIGNAL]" in low_progress_segment.content
    assert "iterations_without_new_typed_record=3" in low_progress_segment.content
    assert "repeated_arg_signature_count=2" in low_progress_segment.content
    assert "unique_tool_call_count_delta=2" in low_progress_segment.content

    non_retrieval_text = "\n".join(
        str(seg.content or "")
        for seg in segments
        if seg.id != "retrieval:low_progress_signal"
    )
    assert "[LOW PROGRESS SIGNAL]" not in non_retrieval_text
