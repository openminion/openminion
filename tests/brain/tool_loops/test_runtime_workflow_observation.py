from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from openminion.modules.brain.loop.adaptive.finalization import (
    _record_workflow_observation,
)
from openminion.modules.brain.loop.constants import WORKFLOW_OBSERVATION_ENABLED_KEY
from openminion.modules.brain.loop.tools import (
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
    DirectToolTurnContext,
)
from openminion.modules.brain.loop.tools.iteration.setup import (
    _loop_request_metadata,
    _workflow_observation_enabled,
)
from openminion.modules.brain.loop.tools.iteration.dispatch import (
    _stage_terminal_request,
)
from openminion.modules.brain.loop.tools.runtime import _normalize_runtime_response
from openminion.modules.brain.loop.tools.response_payloads import (
    _WORKFLOW_LEARNING_GUIDANCE,
)
from openminion.modules.llm.schemas import LLMResponse, Message
from openminion.modules.skill.learning.runtime import (
    WorkflowObservationError,
    WorkflowObservationResult,
)


class _SkillAPI:
    runtime_workflow_observation_enabled = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def observe_workflow(self, **kwargs: object) -> WorkflowObservationResult:
        self.calls.append(dict(kwargs))
        return WorkflowObservationResult(
            status="observed",
            observation_id="wlev-1",
            observation_count=1,
            shape_id="wlsh-1",
            matching_success_count=1,
        )


def _profile(name: str = "general_adaptive_v1") -> SimpleNamespace:
    return SimpleNamespace(profile_name=name)


def _outcome(
    *,
    state: AdaptiveToolLoopState | None = None,
    signal: dict[str, str] | None = None,
) -> AdaptiveToolLoopOutcome:
    return AdaptiveToolLoopOutcome(
        profile_name="general_adaptive_v1",
        mode_name="act_adaptive",
        termination_reason="final_text",
        state=state or AdaptiveToolLoopState(),
        allowed_tools=frozenset({"file.write", "exec.run"}),
        workflow_learning=signal
        or {"intent_category": "modify", "capability_category": "code"},
        final_text="Done.",
    )


def test_workflow_learning_trailer_is_typed_and_hidden_from_answer() -> None:
    response_text = (
        "Done.\n"
        '<workflow_learning>{"intent_category":"modify",'
        '"capability_category":"code"}</workflow_learning>\n'
        '<finalization_status>{"status":"final_answer",'
        '"reasoning":"complete"}</finalization_status>'
    )
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=response_text,
        assistant_messages=[Message(role="assistant", content=response_text)],
        finish_reason="stop",
    )

    normalized = _normalize_runtime_response(
        response,
        workflow_learning_enabled=True,
    )

    assert normalized.output_text == "Done."
    assert normalized.workflow_learning == {
        "intent_category": "modify",
        "capability_category": "code",
    }
    assert normalized.finalization_status["status"] == "final_answer"


def test_native_workflow_learning_signal_is_validated() -> None:
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="Done.",
        workflow_learning={
            "intent_category": "modify",
            "capability_category": "code",
        },
        finish_reason="stop",
    )

    normalized = _normalize_runtime_response(
        response,
        workflow_learning_enabled=True,
    )

    assert normalized.workflow_learning == {
        "intent_category": "modify",
        "capability_category": "code",
    }


def test_native_workflow_learning_signal_hides_invalid_trailer() -> None:
    response_text = "Done.\n<workflow_learning>not-json</workflow_learning>"
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=response_text,
        assistant_messages=[Message(role="assistant", content=response_text)],
        workflow_learning={
            "intent_category": "modify",
            "capability_category": "code",
        },
        finish_reason="stop",
    )

    normalized = _normalize_runtime_response(
        response,
        workflow_learning_enabled=True,
    )

    assert normalized.output_text == "Done."
    assert normalized.assistant_messages[-1].content == "Done."
    assert normalized.workflow_learning == {
        "intent_category": "modify",
        "capability_category": "code",
    }


def test_invalid_native_workflow_learning_signal_is_rejected() -> None:
    for signal in (
        {
            "intent_category": "modify",
            "capability_category": "code",
            "extra": "not allowed",
        },
        {
            "intent_category": "unknown",
            "capability_category": "code",
        },
    ):
        response = LLMResponse(
            ok=True,
            provider="fake",
            model="fake-model",
            output_text="Done.",
            workflow_learning=signal,
            finish_reason="stop",
        )

        normalized = _normalize_runtime_response(
            response,
            workflow_learning_enabled=True,
        )

        assert normalized.output_text == "Done."
        assert normalized.workflow_learning is None


def test_workflow_learning_guidance_freezes_text_trailer_order() -> None:
    assert '<workflow_learning>{"intent_category":"modify"' in (
        _WORKFLOW_LEARNING_GUIDANCE
    )
    assert "immediately before the finalization_status trailer" in (
        _WORKFLOW_LEARNING_GUIDANCE
    )


def test_default_off_preserves_unsolicited_workflow_trailer() -> None:
    response_text = (
        "Answer.\n"
        '<workflow_learning>{"intent_category":"modify",'
        '"capability_category":"code"}</workflow_learning>'
    )
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=response_text,
        assistant_messages=[Message(role="assistant", content=response_text)],
        finish_reason="stop",
    )

    normalized = _normalize_runtime_response(response)

    assert normalized.output_text == response_text
    assert normalized.assistant_messages[-1].content == response_text
    assert normalized.workflow_learning is None


@pytest.mark.parametrize(
    "payload",
    (
        '{"intent_category":"free text","capability_category":"code"}',
        "not-json",
        "[]",
        '"text"',
        "",
    ),
)
def test_enabled_mode_hides_invalid_workflow_trailer(payload: str) -> None:
    response_text = f"Answer.\n<workflow_learning>{payload}</workflow_learning>"
    response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text=response_text,
        assistant_messages=[Message(role="assistant", content=response_text)],
        finish_reason="stop",
    )

    normalized = _normalize_runtime_response(
        response,
        workflow_learning_enabled=True,
    )

    assert normalized.output_text == "Answer."
    assert normalized.assistant_messages[-1].content == "Answer."
    assert normalized.workflow_learning is None


def test_observation_guidance_only_enables_for_opted_in_general_turns() -> None:
    enabled = SimpleNamespace(
        skill_api=SimpleNamespace(runtime_workflow_observation_enabled=True)
    )
    disabled = SimpleNamespace(
        skill_api=SimpleNamespace(runtime_workflow_observation_enabled=False)
    )
    state = AdaptiveToolLoopState()

    assert _workflow_observation_enabled(
        enabled,
        profile=_profile(),
        seeded_queue=[],
        loop_state=state,
    )
    assert not _workflow_observation_enabled(
        disabled,
        profile=_profile(),
        seeded_queue=[],
        loop_state=state,
    )
    assert not _workflow_observation_enabled(
        enabled,
        profile=_profile("coding_adaptive_v1"),
        seeded_queue=[],
        loop_state=state,
    )
    assert not _workflow_observation_enabled(
        enabled,
        profile=_profile(),
        seeded_queue=[object()],
        loop_state=state,
    )


def test_request_metadata_uses_authoritative_observation_eligibility() -> None:
    profile = SimpleNamespace(
        llm_request_overrides={
            "metadata": {WORKFLOW_OBSERVATION_ENABLED_KEY: True, "purpose": "act"}
        }
    )

    disabled = _loop_request_metadata(
        profile,
        [],
        workflow_observation_enabled=False,
    )
    enabled = _loop_request_metadata(
        profile,
        [],
        workflow_observation_enabled=True,
    )

    assert WORKFLOW_OBSERVATION_ENABLED_KEY not in disabled
    assert enabled[WORKFLOW_OBSERVATION_ENABLED_KEY] is True


def test_later_direct_tool_transition_permanently_disables_observation() -> None:
    state = AdaptiveToolLoopState(scratchpad={WORKFLOW_OBSERVATION_ENABLED_KEY: True})
    ctx = SimpleNamespace(state=SimpleNamespace(trace_id="trace-1"))

    _stage_terminal_request(ctx, state, ["file.read"], [])
    state.direct_tool_turn = None

    assert state.scratchpad[WORKFLOW_OBSERVATION_ENABLED_KEY] is False


def test_observation_records_only_successful_substantive_tools() -> None:
    skill_api = _SkillAPI()
    state = AdaptiveToolLoopState(
        scratchpad={
            WORKFLOW_OBSERVATION_ENABLED_KEY: True,
            "adaptive.tool_results": [
                {"tool_name": "plan", "ok": True},
                {"tool_name": "file.write", "ok": True},
                {"tool_name": "exec.run", "ok": False},
            ],
        }
    )
    ctx = SimpleNamespace(
        state=SimpleNamespace(agent_id="agent-a", trace_id="trace-1"),
        _services=SimpleNamespace(runner=SimpleNamespace(skill_api=skill_api)),
    )
    telemetry: dict[str, object] = {}

    _record_workflow_observation(
        ctx,
        loop_outcome=_outcome(state=state),
        telemetry_payload=telemetry,
    )

    assert skill_api.calls == [
        {
            "agent_id": "agent-a",
            "source_run_ref": "trace-1",
            "intent_category": "modify",
            "capability_category": "code",
            "tool_names": ["file.write"],
        }
    ]
    assert telemetry["workflow_learning.status"] == "observed"
    assert "workflow_learning" not in _outcome().telemetry_payload()


@pytest.mark.parametrize(
    "tool_results",
    [
        [],
        [{"tool_name": "exec.run", "ok": False}],
        [{"tool_name": "plan", "ok": True}],
        [
            {
                "tool_name": "file.write",
                "ok": False,
                "status": "approval_pending",
            }
        ],
    ],
)
def test_non_successful_tool_evidence_is_not_observed(
    tool_results: list[dict[str, object]],
) -> None:
    skill_api = _SkillAPI()
    state = AdaptiveToolLoopState(
        scratchpad={
            WORKFLOW_OBSERVATION_ENABLED_KEY: True,
            "adaptive.tool_results": tool_results,
        }
    )
    ctx = SimpleNamespace(
        state=SimpleNamespace(agent_id="agent-a", trace_id="trace-1"),
        _services=SimpleNamespace(runner=SimpleNamespace(skill_api=skill_api)),
    )
    telemetry: dict[str, object] = {}

    _record_workflow_observation(
        ctx,
        loop_outcome=_outcome(state=state),
        telemetry_payload=telemetry,
    )

    assert skill_api.calls == []
    assert telemetry == {"workflow_learning.status": "no_tool_evidence"}


def test_missing_observer_and_missing_signal_do_not_persist() -> None:
    state = AdaptiveToolLoopState(
        scratchpad={
            WORKFLOW_OBSERVATION_ENABLED_KEY: True,
            "adaptive.tool_results": [{"tool_name": "file.write", "ok": True}],
        }
    )
    unavailable_ctx = SimpleNamespace(
        state=SimpleNamespace(agent_id="agent-a", trace_id="trace-1"),
        _services=SimpleNamespace(
            runner=SimpleNamespace(
                skill_api=SimpleNamespace(runtime_workflow_observation_enabled=True)
            )
        ),
    )
    telemetry: dict[str, object] = {}

    _record_workflow_observation(
        unavailable_ctx,
        loop_outcome=_outcome(state=state),
        telemetry_payload=telemetry,
    )
    assert telemetry == {"workflow_learning.status": "skill_api_unavailable"}

    no_signal_telemetry: dict[str, object] = {}
    _record_workflow_observation(
        unavailable_ctx,
        loop_outcome=replace(_outcome(state=state), workflow_learning=None),
        telemetry_payload=no_signal_telemetry,
    )
    assert no_signal_telemetry == {}


def test_direct_turn_and_storage_error_do_not_change_answer_flow() -> None:
    direct_skill = _SkillAPI()
    direct_state = AdaptiveToolLoopState(
        direct_tool_turn=DirectToolTurnContext(
            requested_tool_names=("file.read",),
            requested_batch_signature="file.read",
        ),
        scratchpad={"adaptive.tool_results": [{"tool_name": "file.read", "ok": True}]},
    )
    direct_state.scratchpad[WORKFLOW_OBSERVATION_ENABLED_KEY] = False
    direct_state.direct_tool_turn = None
    direct_ctx = SimpleNamespace(
        state=SimpleNamespace(agent_id="agent-a", trace_id="trace-1"),
        _services=SimpleNamespace(runner=SimpleNamespace(skill_api=direct_skill)),
    )
    _record_workflow_observation(
        direct_ctx,
        loop_outcome=_outcome(state=direct_state),
        telemetry_payload={},
    )
    assert direct_skill.calls == []

    class _FailingSkillAPI(_SkillAPI):
        def observe_workflow(self, **kwargs: object) -> WorkflowObservationResult:
            del kwargs
            raise WorkflowObservationError("storage failed")

    failing_ctx = SimpleNamespace(
        state=SimpleNamespace(agent_id="agent-a", trace_id="trace-2"),
        _services=SimpleNamespace(runner=SimpleNamespace(skill_api=_FailingSkillAPI())),
    )
    telemetry: dict[str, object] = {}
    _record_workflow_observation(
        failing_ctx,
        loop_outcome=_outcome(
            state=AdaptiveToolLoopState(
                scratchpad={
                    WORKFLOW_OBSERVATION_ENABLED_KEY: True,
                    "adaptive.tool_results": [{"tool_name": "file.write", "ok": True}],
                }
            )
        ),
        telemetry_payload=telemetry,
    )
    assert telemetry == {"workflow_learning.status": "error"}
