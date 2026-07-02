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
from openminion.modules.runtime.self_model import (
    DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE,
    SelfModelSnapshot,
    section_degraded,
    section_ok,
)


class _Prefix:
    def build(self, **_kwargs: Any) -> str:
        return "identity prefix"


class _PluginRegistry:
    retriever_names: list[str] = []


def _fit(text: str, cap_tokens: int) -> tuple[str, bool]:
    max_chars = max(0, cap_tokens * 8)
    if max_chars and len(text) > max_chars:
        return text[:max_chars], True
    return text, False


def _tokens(text: str) -> int:
    content = str(text or "")
    return max(1, len(content.split())) if content.strip() else 0


def _project(_: dict[str, Any] | None) -> tuple[Any | None, dict[str, int]]:
    return None, {"raw_chars": 0, "projected_chars": 0, "chars_saved": 0}


def _snapshot() -> SelfModelSnapshot:
    return SelfModelSnapshot.from_sections(
        agent_id="mini",
        identity=section_ok(display_name="Mini", mission="Help.", api_token="secret"),
        capabilities=section_ok(provider="echo", tool_count=4, enabled_tool_count=3),
        policy=section_ok(permission_mode="ask", sandbox="workspace-write"),
        memory_state=section_ok(provider="SQLiteMemoryStore", scopes=["agent:mini"]),
        context_state=section_ok(budget_total=1200),
        knowledge_state=section_ok(providers=[]),
        improvement_state=section_degraded(
            DEGRADED_GENERIC_CANDIDATE_REGISTRY_UNAVAILABLE,
            phase="phase_a_bsil_only",
            policy="never",
            candidate_count=0,
            promotion_posture="bsil_only",
        ),
    )


def _assemble(*, budgets: ContextBudgets) -> tuple[list[Any], dict[str, Any]]:
    request = BuildPackRequest(
        session_id="s-rsai",
        agent_id="mini",
        purpose="decide",
        mode_name="act",
        query="what are you?",
        self_awareness=_snapshot().model_dump(mode="json"),
    )
    segments, _, policy = assemble_segments(
        request=request,
        constraints=BuildConstraints(),
        prompt_tool_schemas=[],
        budgets=budgets,
        bucket_caps=bucket_caps_for(budgets),
        identity_text="identity",
        session_slice=SessionSlice(
            session_id="s-rsai",
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
    if hasattr(policy, "model_dump"):
        return segments, policy.model_dump(mode="json")
    return segments, dict(policy or {})


def test_self_awareness_segment_is_budget_accounted_and_redacted() -> None:
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

    segments, policy = _assemble(budgets=budgets)
    segment = next(item for item in segments if item.id == "self_awareness")

    assert segment.bucket == "self_awareness"
    assert segment.pinned is True
    assert "[SELF AWARENESS]" in segment.content
    assert "secret" not in segment.content
    assert policy.get("actions") or segment.token_estimate <= bucket_caps_for(budgets)[
        "self_awareness"
    ]


def test_self_awareness_segment_truncates_under_tight_budget() -> None:
    budgets = ContextBudgets(
        total_max_tokens=96,
        identity_tokens=16,
        summary_tokens=8,
        conversation_summary_tokens=0,
        active_plan_tokens=0,
        trailer_feedback_tokens=0,
        recent_turn_tokens=16,
        facts_tokens=8,
        memory_tokens=8,
        skills_tokens=0,
        artifact_tokens=0,
        instructions_tokens=8,
    )

    segments, _policy = _assemble(budgets=budgets)
    segment = next(item for item in segments if item.id == "self_awareness")

    assert segment.content.startswith("[SELF AWARENESS]")
    assert len(segment.content) <= bucket_caps_for(budgets)["self_awareness"] * 8
