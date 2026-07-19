from __future__ import annotations

from openminion.modules.brain.runtime.self_awareness import (
    answer_self_awareness_question,
)
from openminion.modules.runtime.self_model import (
    DEGRADED_IDENTITY_UNAVAILABLE,
    SelfModelSnapshot,
    section_degraded,
    section_ok,
)
from openminion.services.agent.context.history import (
    resolve_self_awareness_prompt_answer,
)


def _snapshot() -> SelfModelSnapshot:
    return SelfModelSnapshot.from_sections(
        agent_id="mini",
        identity=section_ok(display_name="Mini", mission="Help the operator."),
        capabilities=section_ok(
            provider="echo", model="echo-small", tool_count=5, enabled_tool_count=3
        ),
        policy=section_ok(
            permission_mode="ask",
            sandbox="workspace-write",
            destructive_action_posture="approval_required",
        ),
        memory_state=section_ok(
            provider="SQLiteMemoryStore",
            provenance_available=True,
            scopes=["agent:mini"],
        ),
        context_state=section_ok(budget_total=4096),
        knowledge_state=section_ok(providers=[]),
        improvement_state=section_degraded(
            "generic_candidate_registry_unavailable",
            policy="never",
            promotion_posture="bsil_only",
        ),
    )


def test_self_awareness_answer_uses_identity_snapshot() -> None:
    answer = answer_self_awareness_question(_snapshot(), question="what are you?")

    assert "I am Mini" in answer
    assert "Help the operator" in answer


def test_self_awareness_answer_reports_visible_tools_only() -> None:
    answer = answer_self_awareness_question(
        _snapshot(), question="what tools do you have?"
    )

    assert "3 enabled tools out of 5 visible tools" in answer
    assert "file_browser" not in answer


def test_self_awareness_answer_reports_degraded_identity_reason() -> None:
    snapshot = _snapshot().model_copy(
        update={
            "identity": section_degraded(
                DEGRADED_IDENTITY_UNAVAILABLE,
                agent_id="mini",
            ),
            "degraded_reasons": [DEGRADED_IDENTITY_UNAVAILABLE],
        }
    )

    answer = answer_self_awareness_question(snapshot, question="what are you?")

    assert "cannot truthfully answer the identity section" in answer
    assert DEGRADED_IDENTITY_UNAVAILABLE in answer


def test_self_awareness_answer_reports_bsil_only_improvement_posture() -> None:
    answer = answer_self_awareness_question(_snapshot(), question="can you improve?")

    assert "promotion posture is bsil_only" in answer
    assert "generic_candidate_registry_unavailable" in answer


def test_prompt_history_helper_delegates_to_snapshot_grounded_answer() -> None:
    answer = resolve_self_awareness_prompt_answer(
        _snapshot().model_dump(mode="json"),
        question="what do you remember?",
    )

    assert "SQLiteMemoryStore" in answer
    assert "Provenance recording is available" in answer
