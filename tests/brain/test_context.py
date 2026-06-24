from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.meta.schemas import MetaConfig
from openminion.modules.brain.runtime.context import build_context
from openminion.modules.brain.schemas import OutcomeAttributionConfig
from openminion.modules.context.schemas import MemoryCard
from openminion.services.lifecycle.self_improvement import ImprovementNote


class _Logger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict, **kwargs) -> None:
        self.events.append((event_type, payload))


class _ContextAPI:
    def __init__(self) -> None:
        self.last_kwargs = {}

    def build(self, **kwargs):
        self.last_kwargs = dict(kwargs)
        return {
            "llm_call_id": "llm-1",
            "pack_version": "pack-1",
            "context_manifest": {
                "memory": ["mem-1", "mem-2"],
                "facts": ["fact-1", "fact-2"],
                "procedures": ["proc-1"],
                "segment_ids": ["static_prefix", "turn_input"],
            },
            "budget_report": {
                "sections": {
                    "identity": {
                        "cap_tokens": 150,
                        "used_tokens": 120,
                        "subsections": {
                            "constraints": {
                                "cap_tokens": 70,
                                "used_tokens": 60,
                                "truncated": False,
                                "omitted_reason": None,
                            }
                        },
                        "ordering_applied": ["constraints"],
                        "unknown_sections": [],
                    }
                }
            },
        }


class _Runner:
    def __init__(self) -> None:
        self.context_api = _ContextAPI()
        self.profile = SimpleNamespace(
            thinking="detailed",
            llm_profiles=SimpleNamespace(
                act_model="MiniMax-M2.5",
                reflect_model="MiniMax-M2.5",
            ),
        )
        self.llm_api = SimpleNamespace(name="openai")
        self.options = SimpleNamespace(
            outcome_attribution_config=OutcomeAttributionConfig(
                max_memory_refs_per_command=3
            )
        )

    def _validate_call_order(self, llm_call_id: str, stage: str) -> dict[str, object]:
        return {"valid": True, "reason": ""}

    def _emit_brain_operation(self, **kwargs) -> bool:
        return True


class _SelfImprovementEngine:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def find_notes_for_context(self, **kwargs):
        self.calls.append(dict(kwargs))
        return [
            ImprovementNote(
                agent_id="agent-1",
                signature="note-1",
                status="active",
                source="tool_failure",
                context="ctx",
                guidance="Validate args before retrying.",
                trigger_tokens=("weather", "city"),
                tags=("tool:weather-openmeteo-current", "error:missing-city"),
                occurrence_count=2,
                apply_count=0,
                created_at="2026-05-08T00:00:00+00:00",
                updated_at="2026-05-08T00:00:01+00:00",
            )
        ]


class _StrategyOutcomeMemoryClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_cross_session_memory_cards_by_type(self, **kwargs):
        self.calls.append(dict(kwargs))
        return [
            MemoryCard(
                record_id="so-1",
                record_type="strategy_outcome",
                text="strategy_outcome_ref",
                meta={
                    "strategy_id": "research",
                    "capability_category": "live_information",
                    "intent_category": "latest_news",
                    "outcome_status": "success",
                    "updated_at": "2026-05-08T00:00:01+00:00",
                },
            )
        ]


class _PostCompletionCritiqueMemoryClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def list_cross_session_memory_cards_by_type(self, **kwargs):
        self.calls.append(dict(kwargs))
        return [
            MemoryCard(
                record_id="critique-1",
                record_type="post_completion_critique",
                text="post_completion_critique_ref",
                meta={
                    "intent_id": "intent-weather",
                    "route_chosen": "act",
                    "sub_intents": ["intent-weather"],
                    "summary": "Validate required inputs before calling the tool.",
                    "lessons": ["Ask for the city before invoking weather."],
                    "created_at": "2026-05-19T00:00:01+00:00",
                },
            )
        ]


def test_context_manifest_event_does_not_forward_legacy_identity_budget_payload() -> (
    None
):
    runner = _Runner()
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-1",
        agent_id="agent-1",
        trace_id=None,
    )

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"tokens": 100},
        hints={"user_input": "hello"},
        logger=logger,
    )

    manifest_events = [
        item for item in logger.events if item[0] == "context.manifest.created"
    ]
    assert manifest_events
    payload = manifest_events[-1][1]
    assert "identity_budget" not in payload
    # The non-budget manifest fields still come through.
    assert payload.get("memory") == ["mem-1", "mem-2"]
    assert payload.get("facts") == ["fact-1", "fact-2"]


def test_context_manifest_records_outcome_attribution_snapshot_for_plan_context() -> (
    None
):
    runner = _Runner()
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-1",
        agent_id="agent-1",
        trace_id=None,
        decision_memory_refs=[],
        decision_context_pack_version=None,
        decision_context_recorded_at=None,
    )

    build_context(
        runner,
        state=state,
        purpose="plan",
        budget={"tokens": 100},
        hints={"user_input": "deploy the release"},
        logger=logger,
    )

    assert state.decision_memory_refs == ["mem-1", "mem-2", "fact-1"]
    assert state.decision_context_pack_version == "pack-1"
    assert state.decision_context_recorded_at


def test_context_manifest_recalled_memory_uses_existing_attribution_state() -> None:
    runner = _Runner()
    runner.context_api.build = lambda **kwargs: {  # type: ignore[method-assign]
        "llm_call_id": "llm-1",
        "pack_version": "pack-recall",
        "context_manifest": {
            "memory": ["mem-query"],
            "recalled_memory": ["mem-recalled"],
            "facts": ["fact-1"],
            "procedures": [],
            "segment_ids": ["static_prefix", "retrieval:memory", "turn_input"],
        },
    }
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-1",
        agent_id="agent-1",
        trace_id=None,
        decision_memory_refs=[],
        decision_context_pack_version=None,
        decision_context_recorded_at=None,
    )

    build_context(
        runner,
        state=state,
        purpose="plan",
        budget={"tokens": 100},
        hints={"user_input": "deploy the release"},
        logger=logger,
    )

    assert state.decision_memory_refs == ["mem-query", "mem-recalled", "fact-1"]
    assert state.decision_context_pack_version == "pack-recall"


def test_non_command_context_does_not_overwrite_outcome_attribution_snapshot() -> None:
    runner = _Runner()
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-2",
        agent_id="agent-1",
        trace_id=None,
        decision_memory_refs=["keep-1", "keep-2"],
        decision_context_pack_version="pack-keep",
        decision_context_recorded_at="2026-03-28T01:02:03+00:00",
    )

    build_context(
        runner,
        state=state,
        purpose="reflect",
        budget={"tokens": 100},
        hints={"user_input": "reflect on the last step"},
        logger=logger,
    )

    assert state.decision_memory_refs == ["keep-1", "keep-2"]
    assert state.decision_context_pack_version == "pack-keep"
    assert state.decision_context_recorded_at == "2026-03-28T01:02:03+00:00"


def test_context_packer_applies_mode_aware_thinking_clamp() -> None:
    runner = _Runner()
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-3",
        agent_id="agent-1",
        trace_id=None,
    )

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"tokens": 100},
        hints={"user_input": "hi", "mode_name": "respond"},
        logger=logger,
    )

    hints = runner.context_api.last_kwargs["hints"]
    assert hints["thinking_effective_profile"] == "minimal"
    assert hints["thinking_mode_default_profile"] == "off"
    assert hints["thinking_mode_allowed_profiles"] == ["off", "minimal"]
    assert hints["thinking_degraded_reason"] == "mode_policy_clamp"


def test_context_packer_surfaces_continuation_guidance_on_decide_reentry() -> None:
    runner = _Runner()
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-continue",
        agent_id="agent-1",
        trace_id=None,
        post_action_user_message=(
            "Continue from the current task state. "
            "Only give a final answer after the task is actually satisfied."
        ),
    )

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"tokens": 100},
        hints={},
        logger=logger,
    )

    hints = runner.context_api.last_kwargs["hints"]
    assert "user_input" not in hints
    assert hints["continuation_guidance"].startswith(
        "Continue from the current task state."
    )


def test_context_packer_surfaces_improvement_note_overlay_from_typed_tool_context() -> (
    None
):
    runner = _Runner()
    runner._self_improvement_engine = _SelfImprovementEngine()  # noqa: SLF001
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-4",
        agent_id="agent-1",
        trace_id=None,
        pending_confirmation_command=SimpleNamespace(
            kind="tool",
            tool_name="weather.openmeteo.current",
        ),
        plan=None,
        cursor=0,
        last_result=SimpleNamespace(
            error=SimpleNamespace(code="missing_city", message="missing city")
        ),
        failure_type=None,
    )

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"tokens": 100},
        hints={"user_input": "check weather"},
        logger=logger,
    )

    hints = runner.context_api.last_kwargs["hints"]
    overlay = hints["live_state_overlay"]
    assert overlay["improvement_note_tool_tags"] == ["tool:weather-openmeteo-current"]
    assert overlay["improvement_note_error_tags"] == ["error:missing-city"]
    assert overlay["improvement_note_cards"][0]["record_id"] == "note-1"
    assert overlay["improvement_note_cards"][0]["meta"]["tool_slugs"] == [
        "weather-openmeteo-current"
    ]


def test_context_packer_surfaces_strategy_outcome_overlay_without_mutating_strategy() -> (
    None
):
    runner = _Runner()
    memctl = _StrategyOutcomeMemoryClient()
    runner.context_api.service = SimpleNamespace(_memctl=memctl)
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-5",
        agent_id="agent-1",
        trace_id=None,
        working_act_profile="research",
        active_mode_name="research",
        decision_reason_code="latest_news",
    )

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"tokens": 100},
        hints={
            "user_input": "check latest news",
            "capability_category": "live_information",
        },
        logger=logger,
    )

    hints = runner.context_api.last_kwargs["hints"]
    overlay = hints["live_state_overlay"]
    assert overlay["strategy_outcome_strategy_id"] == "research"
    assert overlay["strategy_outcome_capability_category"] == "live_information"
    assert overlay["strategy_outcome_intent_category"] == "latest_news"
    assert overlay["strategy_outcome_cards"][0]["record_id"] == "so-1"
    assert overlay["strategy_outcome_cards"][0]["meta"]["outcome_status"] == "success"
    assert memctl.calls[0]["record_types"] == ["strategy_outcome"]
    assert "session_id" not in memctl.calls[0]
    assert state.working_act_profile == "research"


def test_context_packer_surfaces_post_completion_critique_overlay() -> None:
    runner = _Runner()
    memctl = _PostCompletionCritiqueMemoryClient()
    runner.context_api.service = SimpleNamespace(_memctl=memctl)
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-pccm",
        agent_id="agent-1",
        trace_id=None,
        active_mode_name="act",
        decision_sub_intents=["intent-weather"],
        intent_execution_states=[SimpleNamespace(intent_id="intent-weather")],
    )

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"tokens": 100},
        hints={"user_input": "check weather"},
        logger=logger,
    )

    hints = runner.context_api.last_kwargs["hints"]
    overlay = hints["live_state_overlay"]
    assert overlay["post_completion_critique_intent_ids"] == ["intent-weather"]
    assert overlay["post_completion_critique_sub_intents"] == ["intent-weather"]
    assert overlay["post_completion_critique_route"] == "act"
    assert overlay["post_completion_critique_cards"][0]["record_id"] == "critique-1"
    assert overlay["post_completion_critique_cards"][0]["meta"]["intent_id"] == (
        "intent-weather"
    )
    assert any(
        call.get("record_types") == ["post_completion_critique"]
        for call in memctl.calls
    )


def test_context_packer_surfaces_low_progress_and_learning_metrics() -> None:
    runner = _Runner()
    runner.context_api.service = SimpleNamespace(_memctl=_StrategyOutcomeMemoryClient())
    runner._self_improvement_engine = _SelfImprovementEngine()  # noqa: SLF001
    runner.meta_engine = SimpleNamespace(
        cfg=MetaConfig(
            low_progress_iterations_without_new_typed_record_threshold=3,
            low_progress_repeated_arg_signature_threshold=2,
            low_progress_unique_tool_call_count_delta_threshold=2,
        )
    )
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-7",
        agent_id="agent-1",
        trace_id=None,
        working_act_profile="research",
        active_mode_name="research",
        decision_reason_code="latest_news",
        pending_confirmation_command=SimpleNamespace(
            kind="tool",
            tool_name="weather.openmeteo.current",
        ),
        plan=None,
        cursor=0,
        retries_for_step={"step-1": 3},
        last_result=SimpleNamespace(
            status="failed",
            error=SimpleNamespace(code="ERR", message="bad"),
            facts=[],
        ),
        module_state={
            "adaptive_loop": {
                "tool_call_history": [
                    {
                        "tool_name": "web.search",
                        "args_hash": "dup-1",
                        "result_summary": "no result",
                    },
                    {
                        "tool_name": "web.search",
                        "args_hash": "dup-1",
                        "result_summary": "no result",
                    },
                    {
                        "tool_name": "web.search",
                        "args_hash": "dup-1",
                        "result_summary": "no result",
                    },
                ],
                "budgets_consumed": {"tool_calls": 3},
            }
        },
        failure_type=None,
        decision_memory_refs=["mem-1", "mem-2"],
    )

    build_context(
        runner,
        state=state,
        purpose="decide",
        budget={"tokens": 100},
        hints={
            "user_input": "check latest news",
            "capability_category": "live_information",
        },
        logger=logger,
    )

    overlay = runner.context_api.last_kwargs["hints"]["live_state_overlay"]
    assert overlay["low_progress_signal"]["iterations_without_new_typed_record"] == 3
    assert overlay["low_progress_signal"]["repeated_arg_signature_count"] == 2
    assert overlay["low_progress_signal"]["unique_tool_call_count_delta"] == 2
    assert overlay["learning_loop_metric"]["strategy_outcome_count"] == 1
    assert overlay["learning_loop_metric"]["improvement_note_count"] == 1
    assert overlay["learning_loop_metric"]["decision_memory_ref_count"] == 2
    # Per CSRR: the boolean reflects the dedicated cross-session recall path,
    # so the truthful field name is cross-session again.
    assert (
        overlay["learning_loop_metric"]["cross_session_strategy_outcomes_present"]
        is True
    )
    assert "session_strategy_outcomes_present" not in overlay["learning_loop_metric"]


def test_learning_loop_metric_schema_pins_truthful_field_names() -> None:
    from openminion.modules.brain.schemas import LearningLoopMetric

    fields = set(LearningLoopMetric.model_fields.keys())
    assert "cross_session_strategy_outcomes_present" in fields, (
        "LearningLoopMetric must expose the truthful cross-session boolean."
    )
    assert "session_strategy_outcomes_present" not in fields, (
        "The temporary session-scoped field name must not remain after the"
        " retrieval substrate is lifted to a truthful cross-session owner."
    )
    # Field stays a bool defaulting to False so the metric is honest when no
    # cross-session strategy-outcome cards are present.
    metric = LearningLoopMetric()
    assert metric.cross_session_strategy_outcomes_present is False


def test_context_packer_surfaces_budget_telemetry_when_enabled() -> None:
    runner = _Runner()
    runner.profile.budgets = SimpleNamespace(
        max_ticks_per_user_turn=24,
        max_tool_calls=8,
        max_total_llm_tokens=2400,
        max_elapsed_ms=120000,
    )
    runner.profile.budget_telemetry = {"enabled": True, "granularity": "fine"}
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-6",
        agent_id="agent-1",
        trace_id=None,
        budgets_remaining=SimpleNamespace(
            ticks=2,
            tool_calls=3,
            tokens=900,
            time_ms=40000,
        ),
        mission=None,
    )

    build_context(
        runner,
        state=state,
        purpose="act",
        budget={"tokens": 100},
        hints={"user_input": "keep working"},
        logger=logger,
    )

    payload = runner.context_api.last_kwargs["hints"]["budget_telemetry"]
    assert payload["iteration_used"] == 22
    assert payload["iteration_remaining"] == 2
    assert payload["iteration_max"] == 24
    assert payload["tool_calls_used"] == 5
    assert payload["tool_calls_remaining"] == 3
    assert payload["tool_calls_max"] == 8
    assert payload["token_used"] == 1500
    assert payload["token_remaining"] == 900
    assert payload["token_max"] == 2400
    assert payload["time_elapsed_ms"] == 80000
    assert payload["time_remaining_ms"] == 40000
    assert payload["budget_envelope_status"] == "near_exhaustion"


def test_context_packer_omits_budget_telemetry_when_disabled() -> None:
    runner = _Runner()
    runner.profile.budgets = SimpleNamespace(
        max_ticks_per_user_turn=24,
        max_tool_calls=8,
        max_total_llm_tokens=2400,
        max_elapsed_ms=120000,
    )
    runner.profile.budget_telemetry = {"enabled": False, "granularity": "fine"}
    logger = _Logger()
    state = SimpleNamespace(
        unresolved_clarify_items=[],
        clarify_responses={},
        session_id="sess-7",
        agent_id="agent-1",
        trace_id=None,
        budgets_remaining=SimpleNamespace(
            ticks=12,
            tool_calls=4,
            tokens=1200,
            time_ms=60000,
        ),
        mission=None,
    )

    build_context(
        runner,
        state=state,
        purpose="act",
        budget={"tokens": 100},
        hints={"user_input": "keep working"},
        logger=logger,
    )

    assert "budget_telemetry" not in runner.context_api.last_kwargs["hints"]
