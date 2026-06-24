from __future__ import annotations

from typing import Any

from openminion.modules.brain.execution.memory import write_decision_memory
from openminion.modules.brain.schemas import ActDecision, BudgetCounters, WorkingState
from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    MemoryCard,
    SessionSlice,
    bucket_caps_for,
)
from openminion.modules.context.segment import (
    _rank_decision_memory_cards,
    assemble_segments,
)


class _Prefix:
    def build(self, **kwargs: Any) -> str:
        del kwargs
        return "identity"


class _PluginRegistry:
    retriever_names: list[str] = []


class _MemoryAPI:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def put_record(self, **kwargs: Any) -> str:
        self.records.append(dict(kwargs))
        return f"decision-{len(self.records)}"


def _fit(text: str, cap_tokens: int) -> tuple[str, bool]:
    max_chars = max(0, int(cap_tokens or 0) * 10)
    if max_chars and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _tokens(text: str) -> int:
    return max(1, len(str(text or "").split())) if str(text or "").strip() else 0


def _project(_: dict[str, Any] | None) -> tuple[Any | None, dict[str, int]]:
    return None, {"raw_chars": 0, "projected_chars": 0, "chars_saved": 0}


def _decision_card(
    record_id: str,
    *,
    reason_code: str,
    created_at: str,
    text: str = "",
    sub_intents: list[str] | None = None,
) -> MemoryCard:
    return MemoryCard(
        record_id=record_id,
        record_type="decision",
        text=text or f"Decision {record_id}",
        meta={
            "route_chosen": "act",
            "reason_code": reason_code,
            "sub_intents": list(sub_intents or []),
            "rationale": text,
            "created_at": created_at,
        },
    )


def _segments_with_cards(
    *,
    memory_cards: list[MemoryCard],
    live_state_overlay: dict[str, Any],
    active_state: dict[str, Any] | None = None,
) -> list[Any]:
    request = BuildPackRequest(
        session_id="s-drm-03",
        agent_id="agent-drm",
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
            session_id="s-drm-03",
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


def test_decision_memory_ranking_uses_structural_metadata_not_lexical_overlap() -> None:
    request = BuildPackRequest(
        session_id="s-drm-03",
        agent_id="agent-drm",
        purpose="decide",
        mode_name="act",
        query="trip budget japan contains old-card words",
        live_state_overlay={
            "decision_reason_code": "structured_followup",
            "decision_sub_intents": ["structured_budget"],
        },
    )
    lexical_only = _decision_card(
        "old-lexical",
        reason_code="unrelated",
        created_at="2026-05-01T00:00:00+00:00",
        text="trip budget japan contains old-card words",
        sub_intents=["unrelated"],
    )
    structural = _decision_card(
        "new-structural",
        reason_code="structured_followup",
        created_at="2026-05-01T00:00:01+00:00",
        text="different words entirely",
        sub_intents=["structured_budget"],
    )

    ranked = _rank_decision_memory_cards(
        [lexical_only, structural],
        request=request,
    )

    assert [item.record_id for item in ranked] == ["new-structural", "old-lexical"]


def test_decide_context_surfaces_decision_memory_bucket() -> None:
    segments = _segments_with_cards(
        live_state_overlay={
            "decision_reason_code": "structured_followup",
            "decision_sub_intents": ["structured_budget"],
        },
        memory_cards=[
            _decision_card(
                "decision-1",
                reason_code="structured_followup",
                created_at="2026-05-01T00:00:01+00:00",
                text="model-authored rationale",
                sub_intents=["structured_budget"],
            )
        ],
    )

    decision_segment = next(seg for seg in segments if seg.id == "retrieval:decisions")
    assert decision_segment.bucket == "retrieval"
    assert decision_segment.refs == ["decision-1"]
    assert "[DECISION MEMORY]" in decision_segment.content
    assert "reason_code=structured_followup" in decision_segment.content


def test_written_decision_card_surfaces_for_repeated_typed_sub_intent() -> None:
    memory = _MemoryAPI()
    state = WorkingState(
        session_id="s-drm-04",
        agent_id="agent-drm",
        trace_id="trace-drm-04",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=4,
            a2a_calls=2,
            tokens=4000,
            time_ms=60_000,
        ),
    )
    decision = ActDecision(
        reason_code="structured_followup",
        sub_intents=["structured_budget"],
        rationale="The model chose structured budget work.",
        act_profile="general",
    )

    write_decision_memory(
        runner=type("Runner", (), {"memory_api": memory})(),
        state=state,
        decision=decision,
    )
    stored = memory.records[0]
    content = stored["content"]
    segments = _segments_with_cards(
        live_state_overlay={
            "decision_reason_code": "structured_followup",
            "decision_sub_intents": ["structured_budget"],
        },
        memory_cards=[
            MemoryCard(
                record_id="decision-1",
                record_type="decision",
                text=str(content["rationale"]),
                meta=dict(content),
            )
        ],
    )

    decision_segment = next(seg for seg in segments if seg.id == "retrieval:decisions")
    assert "sub_intents=structured_budget" in decision_segment.content
    assert "rationale=The model chose structured budget work." in (
        decision_segment.content
    )
    assert "user_intent" not in decision_segment.content


def test_decide_context_surfaces_active_state_decision_memory_ref() -> None:
    segments = _segments_with_cards(
        live_state_overlay={
            "decision_reason_code": "entry_text_response",
            "decision_memory_refs": ["mem-decision-1"],
            "decision_context_recorded_at": "2026-05-01T00:00:01+00:00",
        },
        active_state={
            "active_mode_name": "respond",
            "decision_reason_code": "entry_text_response",
            "decision_memory_refs": ["mem-decision-1"],
            "decision_context_recorded_at": "2026-05-01T00:00:01+00:00",
        },
        memory_cards=[],
    )

    decision_segment = next(seg for seg in segments if seg.id == "retrieval:decisions")
    assert decision_segment.refs == ["mem-decision-1"]
    assert "reason_code=entry_text_response" in decision_segment.content
