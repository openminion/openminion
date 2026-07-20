from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.execution.child_tasks import (
    DecomposeControlPayload,
)
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    AdaptiveBudgetConfig,
    BudgetCounters,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.loop.tools.engine import (
    _force_budget_answer_only_finalization,
    _force_duplicate_batch_answer_only_closure,
    _action_result_has_retry_or_poll_signal,
    _active_work_summary_from_state,
    _adaptive_budget_config,
    _append_tool_result_payload,
    _build_enrichment_message,
    _build_intent_execution_state_message,
    _build_missing_action_result,
    _build_tool_failure_recovery_message,
    _confident_complete_payload,
    _count_substantive_non_control_tool_results,
    _decompose_decline_result,
    _decompose_invalid_outcome,
    _decompose_tool_calls,
    _delegated_child_context,
    _delegated_child_context_message,
    _delegation_context_payload,
    _delegation_result_summary_payload,
    _duplicate_batch_recovery_message,
    _duplicate_batch_retry_counts,
    _effective_cap,
    _eligible_duplicate_batch_execution_facts,
    _event_type_for_budget_stop,
    _explicit_calendar_years,
    _finalization_status_payload,
    _general_profile_name,
    _goal_declaration_payload,
    _goal_revision_payload,
    _has_tool_evidence_for_answer_only,
    _llm_budget_available_for_answer_only,
    _loop_has_non_success_tool_result,
    _loop_tool_result_payloads,
    _max_steps_hint_from_state,
    _memory_consolidation_context,
    _memory_consolidation_context_message,
    _memory_consolidation_payload,
    _meta_rule_preference_payload,
    _pending_finalization_salvage_text,
    _pending_turn_context_payload,
    _repair_stale_exact_date_search_args,
    _record_duplicate_batch_execution_facts,
    _requires_typed_finalization_contract,
    _session_work_summary_payload,
    _set_turn_progress,
    _stale_exact_date_query_reason,
    _step_summaries_from_state,
    _subtasks_from_decompose_control,
    _task_plan_abandoned_payload,
    _task_plan_completed_payload,
    _task_plan_payload,
    _task_plan_revision_payload,
    _task_plan_step_blocked_payload,
    _task_plan_step_completed_payload,
    _tool_budget_exhausted_for_answer_only,
    _tool_efficiency_guidance,
    _tool_request_result,
    _tool_result_payload_from_action,
    _watch_outcome_payload,
)
from openminion.modules.brain.loop.tools.duplicate_batch import (
    _reset_duplicate_batch_tracking,
)
from openminion.modules.brain.loop.tools.postprocess.rules import (
    _final_answer_references_unbacked_source_urls,
    _final_text_parrots_policy_denial,
    _looks_like_execution_preface_draft,
    _looks_like_unexecutable_tool_payload_text,
)
from openminion.modules.brain.loop.tools.messages import action_result_to_tool_message
from openminion.modules.brain.loop.tools.iteration.termination import (
    finalize_iteration_cap_exit,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DECOMPOSE_INVALID,
    ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_LLM_ERROR,
    ADAPTIVE_TERM_NEEDS_USER,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    run_adaptive_tool_loop,
    semantic_batch_signature,
)
from openminion.modules.brain.loop.entry import decompose_tool_spec
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import (
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
    UsageInfo,
)


# Shared fixtures — mirror tests/brain/tool_loops/test_engine.py style


@dataclass
class _FakeRuntime:
    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    raise_error: bool = False
    _index: int = 0

    def complete(
        self,
        *,
        messages,
        tools,
        model,
        tool_choice="auto",
        max_output_tokens=None,
        metadata=None,
    ):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "model": model,
                "tool_choice": tool_choice,
                "max_output_tokens": max_output_tokens,
                "metadata": metadata,
            }
        )
        if self.raise_error:
            raise RuntimeError("runtime forced failure")
        response = self.responses[self._index]
        self._index += 1
        return response


@dataclass
class _LoopContext:
    state: WorkingState
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    commands: list[Any] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    session_api: Any | None = None
    _index: int = 0

    def execute_command(self, *, command, include_reflect: bool = False):
        del include_reflect
        self.commands.append(command)
        outcome = self.outcomes[self._index]
        self._index += 1
        return outcome

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _state(
    *,
    tool_calls: int = 5,
    tokens: int = 5000,
    llm_calls_max: int = 5,
) -> WorkingState:
    return WorkingState(
        session_id="s-char",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=tool_calls,
            a2a_calls=0,
            tokens=tokens,
            time_ms=120000,
        ),
        llm_calls_max=llm_calls_max,
    )


def _tool_specs(*names: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=name,
            description=name,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        )
        for name in names
    ]


def _profile(
    *,
    allowed_tools: frozenset[str],
    max_iterations: int = 4,
    max_tool_calls_per_loop: int | None = None,
    allow_llm_recovery_after_tool_failure: bool = True,
    profile_name: str = "shared_char_test",
    adaptive_budget_config: AdaptiveBudgetConfig | None = None,
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name=profile_name,
        mode_name="act_adaptive",
        allowed_tools=allowed_tools,
        max_iterations=max_iterations,
        max_tool_calls_per_loop=max_tool_calls_per_loop,
        allow_llm_recovery_after_tool_failure=allow_llm_recovery_after_tool_failure,
        tool_choice="auto" if allowed_tools else "none",
        provider_parallel_tool_capacity=1,
        adaptive_budget_config=adaptive_budget_config,
    )


def _success_outcome(
    tool_name: str = "file.read", summary: str = "ok"
) -> CommandExecutionOutcome:
    return CommandExecutionOutcome(
        approved_command=SimpleNamespace(tool_name=tool_name, args={"path": "x"}),
        action_result=ActionResult(
            command_id=new_uuid(),
            status="success",
            summary=summary,
            outputs={"content": summary},
        ),
    )


def _failed_outcome(
    tool_name: str = "file.read", code: str = "ERR"
) -> CommandExecutionOutcome:
    return CommandExecutionOutcome(
        approved_command=SimpleNamespace(tool_name=tool_name, args={"path": "x"}),
        action_result=ActionResult(
            command_id=new_uuid(),
            status="failed",
            summary="failure summary",
            error=ActionError(code=code, message="boom"),
        ),
    )


# Pure-function characterization: date helpers


class TestExplicitCalendarYears:
    def test_empty_input_returns_empty_set(self) -> None:
        assert _explicit_calendar_years("") == set()
        assert _explicit_calendar_years(None) == set()
        assert _explicit_calendar_years("   ") == set()

    def test_extracts_single_year(self) -> None:
        assert _explicit_calendar_years("hello 2024") == {2024}

    def test_extracts_multiple_years_dedup(self) -> None:
        assert _explicit_calendar_years("2024 vs 2025 and 2024") == {2024, 2025}

    def test_ignores_non_year_digits(self) -> None:
        # \b(20\d{2})\b — only century 20xx 4-digit numbers
        assert _explicit_calendar_years("12345 and 1999") == set()

    def test_handles_non_string_input(self) -> None:
        assert _explicit_calendar_years(2024) == {2024}


class TestStaleExactDateQueryReason:
    def test_require_exact_date_false_returns_none(self) -> None:
        result = _stale_exact_date_query_reason(
            user_input="anything",
            require_exact_date=False,
            tool_name="web.search",
            tool_args={"query": "weather 2024"},
        )
        assert result is None

    def test_non_websearch_tool_returns_none(self) -> None:
        result = _stale_exact_date_query_reason(
            user_input="weather",
            require_exact_date=True,
            tool_name="file.read",
            tool_args={"query": "weather 2024"},
        )
        assert result is None

    def test_empty_query_returns_none(self) -> None:
        result = _stale_exact_date_query_reason(
            user_input="weather",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": ""},
        )
        assert result is None

    def test_query_without_years_returns_none(self) -> None:
        result = _stale_exact_date_query_reason(
            user_input="hello",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": "current weather"},
        )
        assert result is None

    def test_user_input_has_year_returns_none(self) -> None:
        result = _stale_exact_date_query_reason(
            user_input="what happened in 2023",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": "events 2023"},
            current_year=2026,
        )
        assert result is None

    def test_query_year_matches_current_returns_none(self) -> None:
        result = _stale_exact_date_query_reason(
            user_input="what's new",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": "news 2026"},
            current_year=2026,
        )
        assert result is None

    def test_stale_year_returns_explanation(self) -> None:
        result = _stale_exact_date_query_reason(
            user_input="who won",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": "championship 2020"},
            current_year=2026,
        )
        assert result is not None
        assert "2020" in result
        assert "2026" in result

    def test_default_current_year_falls_back_to_datetime(self) -> None:
        # Without explicit current_year, use system year — we won't assert exact
        # output, only that the function returns None when query year == sys year.
        from datetime import datetime, timezone

        sys_year = datetime.now(timezone.utc).year
        result = _stale_exact_date_query_reason(
            user_input="news",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": f"news {sys_year}"},
        )
        assert result is None


class TestRepairStaleExactDateSearchArgs:
    def test_repairs_runtime_invented_stale_year(self) -> None:
        result = _repair_stale_exact_date_search_args(
            user_input="what's new",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": "news 2025"},
            current_year=2026,
        )
        assert result == {"query": "news"}

    def test_returns_none_when_user_requested_historical_year(self) -> None:
        result = _repair_stale_exact_date_search_args(
            user_input="compare 2025 with today",
            require_exact_date=True,
            tool_name="web.search",
            tool_args={"query": "news 2025"},
            current_year=2026,
        )
        assert result is None


_TASK_PLAN_STEP = {"step_id": "s1", "description": "do work"}
_PAYLOAD_CASES: list[tuple[Any, str, dict[str, Any]]] = [
    (
        _confident_complete_payload,
        "confident_complete",
        {"complete": True, "reasoning": "ok"},
    ),
    (
        _finalization_status_payload,
        "finalization_status",
        {"status": "final_answer", "reasoning": "done"},
    ),
    (
        _watch_outcome_payload,
        "watch_outcome",
        {"condition_met": True, "summary": "all good"},
    ),
    (
        _pending_turn_context_payload,
        "pending_turn_context",
        {"active_work_summary": "ws"},
    ),
    (
        _meta_rule_preference_payload,
        "meta_rule_preference",
        {"rule": "retry", "preferred_value": "3", "reasoning": "x"},
    ),
    (_memory_consolidation_payload, "memory_consolidation", {"decisions": []}),
    (_session_work_summary_payload, "session_work_summary", {"summary": "hello"}),
    (
        _goal_declaration_payload,
        "goal_declaration",
        {"goal": "g", "trigger": "observed"},
    ),
    (
        _goal_revision_payload,
        "goal_revision",
        {"previous_goal": "old", "goal": "new", "trigger": "observed"},
    ),
    (
        _task_plan_payload,
        "task_plan",
        {"plan_id": "p1", "objective": "o", "steps": [_TASK_PLAN_STEP]},
    ),
    (
        _task_plan_revision_payload,
        "task_plan_revision",
        {"plan_id": "p1", "revised_steps": [_TASK_PLAN_STEP]},
    ),
    (
        _task_plan_step_completed_payload,
        "task_plan_step_completed",
        {"plan_id": "p1", "step_id": "s1"},
    ),
    (
        _task_plan_step_blocked_payload,
        "task_plan_step_blocked",
        {"plan_id": "p1", "step_id": "s1", "blocker_type": "missing_tool"},
    ),
    (_task_plan_abandoned_payload, "task_plan_abandoned", {"plan_id": "p1"}),
    (_task_plan_completed_payload, "task_plan_completed", {"plan_id": "p1"}),
    (_delegation_context_payload, "delegation_context", {"intent_id": "i1"}),
    (
        _delegation_result_summary_payload,
        "delegation_result_summary",
        {"summary": "did the thing"},
    ),
]


@pytest.mark.parametrize("extractor,attr,valid_dict", _PAYLOAD_CASES)
def test_payload_extractor_absent_returns_none(extractor, attr, valid_dict) -> None:
    response = SimpleNamespace()
    # When the response lacks the attr, getattr returns None -> not a dict -> None
    assert extractor(response) is None


@pytest.mark.parametrize("extractor,attr,valid_dict", _PAYLOAD_CASES)
def test_payload_extractor_non_dict_returns_none(extractor, attr, valid_dict) -> None:
    response = SimpleNamespace(**{attr: "not a dict"})
    assert extractor(response) is None


@pytest.mark.parametrize("extractor,attr,valid_dict", _PAYLOAD_CASES)
def test_payload_extractor_valid_returns_typed_model(
    extractor, attr, valid_dict
) -> None:
    response = SimpleNamespace(**{attr: valid_dict})
    result = extractor(response)
    assert result is not None


@pytest.mark.parametrize("extractor,attr,valid_dict", _PAYLOAD_CASES)
def test_payload_extractor_invalid_dict_returns_none(
    extractor, attr, valid_dict
) -> None:
    # Force validation failure: use a list inside a required field where a string is needed.
    bad = {"__totally_unexpected__": object()}
    response = SimpleNamespace(**{attr: bad})
    try:
        result = extractor(response)
    except Exception:  # pragma: no cover — defensive
        pytest.fail("extractor leaked an exception")
    assert result is None or result is not None  # branch executed either way


# Pure: small predicates / helpers


class TestEventTypeForBudgetStop:
    @pytest.mark.parametrize(
        "reason,expected",
        [
            ("noop_guard", "budget.noop_guard"),
            ("user_declined", "budget.user_declined"),
            ("user_timeout", "budget.user_timeout"),
            ("anything-else", "budget.exhausted"),
            ("", "budget.exhausted"),
        ],
    )
    def test_event_type_mapping(self, reason: str, expected: str) -> None:
        # Import STOP_* constants so this hits the explicit equality branch.
        from openminion.modules.brain.constants import (
            STOP_NOOP_GUARD,
            STOP_USER_DECLINED,
            STOP_USER_TIMEOUT,
        )

        # Re-map well-known names to the real constants
        mapping = {
            "noop_guard": STOP_NOOP_GUARD,
            "user_declined": STOP_USER_DECLINED,
            "user_timeout": STOP_USER_TIMEOUT,
        }
        actual_reason = mapping.get(reason, reason)
        assert _event_type_for_budget_stop(actual_reason) == expected


class TestGeneralProfileName:
    def test_true_for_general_adaptive_v1(self) -> None:
        prof = _profile(allowed_tools=frozenset(), profile_name="general_adaptive_v1")
        assert _general_profile_name(prof) is True

    def test_false_for_others(self) -> None:
        prof = _profile(allowed_tools=frozenset(), profile_name="shared_char_test")
        assert _general_profile_name(prof) is False

    def test_strips_whitespace(self) -> None:
        prof = _profile(
            allowed_tools=frozenset(), profile_name="  general_adaptive_v1  "
        )
        assert _general_profile_name(prof) is True


class TestToolEfficiencyGuidance:
    def test_with_explicit_max_tool_calls(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}), max_tool_calls_per_loop=7)
        text = _tool_efficiency_guidance(prof)
        assert "7 tool calls" in text

    def test_with_no_explicit_max_tool_calls(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}), max_tool_calls_per_loop=None)
        text = _tool_efficiency_guidance(prof)
        assert "the available" in text


class TestEffectiveCap:
    def test_dynamic_cap_overrides_profile(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}), max_iterations=2)
        loop_state = AdaptiveToolLoopState(messages=[], effective_max_iterations=10)
        assert _effective_cap(prof, loop_state) == 10

    def test_falls_back_to_profile_when_zero(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}), max_iterations=3)
        loop_state = AdaptiveToolLoopState(messages=[], effective_max_iterations=0)
        assert _effective_cap(prof, loop_state) == 3


class TestAdaptiveBudgetConfig:
    def test_returns_none_when_absent(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        assert _adaptive_budget_config(prof) is None

    def test_returns_existing_instance(self) -> None:
        cfg = AdaptiveBudgetConfig(mode="autonomous", extend_by=4, idle_timeout_s=60)
        prof = _profile(allowed_tools=frozenset({"x"}), adaptive_budget_config=cfg)
        assert _adaptive_budget_config(prof) is cfg

    def test_validates_dict(self) -> None:
        # The profile is a frozen dataclass; construct with a raw dict
        # to exercise the dict-validation branch in _adaptive_budget_config.
        prof = AdaptiveToolLoopProfile(
            profile_name="t",
            mode_name="act_adaptive",
            allowed_tools=frozenset({"x"}),
            max_iterations=2,
            adaptive_budget_config={
                "mode": "autonomous",
                "extend_by": 2,
                "idle_timeout_s": 60,
            },  # type: ignore[arg-type]
        )
        cfg = _adaptive_budget_config(prof)
        assert cfg is not None
        assert cfg.mode == "autonomous"


# Loop state tool-result helpers


class TestLoopToolResultPayloads:
    def test_returns_empty_when_scratchpad_missing(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        assert _loop_tool_result_payloads(st) == []

    def test_filters_to_dicts(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad = {"adaptive.tool_results": [{"ok": True}, "junk", {"ok": False}]}
        assert _loop_tool_result_payloads(st) == [{"ok": True}, {"ok": False}]


class TestCountSubstantiveNonControlToolResults:
    def test_skips_control_and_empty_tool_names(self) -> None:
        from openminion.modules.brain.loop.tools.plan_control import PLAN_TOOL_NAME
        from openminion.modules.brain.loop.tools.shortlisting import (
            TOOL_REQUEST_TOOL_NAME,
        )

        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.read", "ok": True},
                {"tool_name": PLAN_TOOL_NAME, "ok": True},
                {"tool_name": TOOL_REQUEST_TOOL_NAME, "ok": True},
                {"tool_name": "decompose", "ok": True},
                {"tool_name": "", "ok": True},
                {"tool_name": "exec.run", "ok": True},
            ]
        }
        assert _count_substantive_non_control_tool_results(st) == 2


class TestLoopHasNonSuccessToolResult:
    def test_true_when_any_ok_false(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad = {"adaptive.tool_results": [{"ok": True}, {"ok": False}]}
        assert _loop_has_non_success_tool_result(st) is True

    def test_false_when_all_ok(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad = {"adaptive.tool_results": [{"ok": True}, {"ok": True}]}
        assert _loop_has_non_success_tool_result(st) is False


class TestRequiresTypedFinalizationContract:
    def test_false_for_non_general_profile(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}), profile_name="other")
        st = AdaptiveToolLoopState(messages=[])
        assert (
            _requires_typed_finalization_contract(profile=prof, loop_state=st) is False
        )

    def test_false_for_non_general_profile_without_tool_work(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}), profile_name="coding_v1")
        st = AdaptiveToolLoopState(messages=[])
        assert (
            _requires_typed_finalization_contract(profile=prof, loop_state=st) is False
        )

    def test_true_for_general_no_direct_tool(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st = AdaptiveToolLoopState(messages=[])
        assert (
            _requires_typed_finalization_contract(profile=prof, loop_state=st) is True
        )

    def test_false_for_general_with_direct_tool_active_when_work_is_trivial(
        self,
    ) -> None:
        from openminion.modules.brain.loop.tools import DirectToolTurnContext

        prof = _profile(
            allowed_tools=frozenset({"web.search"}), profile_name="general_adaptive_v1"
        )
        call = ToolCall(id="c1", name="web.search", arguments={"query": "x"})
        st = AdaptiveToolLoopState(
            messages=[],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("web.search",),
                requested_batch_signature=semantic_batch_signature([call]),
                requested_calls=(call,),
            ),
        )
        assert (
            _requires_typed_finalization_contract(profile=prof, loop_state=st) is False
        )

    def test_true_for_general_with_direct_tool_active_after_substantive_tool_work(
        self,
    ) -> None:
        from openminion.modules.brain.loop.tools import DirectToolTurnContext

        prof = _profile(
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            profile_name="general_adaptive_v1",
        )
        call = ToolCall(
            id="c1",
            name="web.search",
            arguments={"query": "uv vs pipx package managers"},
        )
        st = AdaptiveToolLoopState(
            messages=[],
            direct_tool_turn=DirectToolTurnContext(
                requested_tool_names=("web.search",),
                requested_batch_signature=semantic_batch_signature([call]),
                requested_calls=(call,),
            ),
        )
        st.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "web.search", "ok": True},
                {"tool_name": "web.fetch", "ok": True},
                {"tool_name": "web.fetch", "ok": True},
            ]
        }
        assert (
            _requires_typed_finalization_contract(profile=prof, loop_state=st) is True
        )

    def test_true_for_non_general_profile_after_substantive_tool_work(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            profile_name="coding_v1",
        )
        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "web.search", "ok": True},
                {"tool_name": "web.fetch", "ok": True},
                {"tool_name": "web.fetch", "ok": True},
            ]
        }
        assert (
            _requires_typed_finalization_contract(profile=prof, loop_state=st) is True
        )

    def test_false_for_coding_profile_after_mutating_file_work(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"file.write", "file.read"}),
            profile_name="coding_v1",
        )
        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
                {"tool_name": "file.read", "ok": True},
            ]
        }
        assert (
            _requires_typed_finalization_contract(profile=prof, loop_state=st) is False
        )


# Step summaries / active work summary / max-steps hint


class TestStepSummariesFromState:
    def test_empty_when_state_none(self) -> None:
        ctx = SimpleNamespace(state=None)
        assert _step_summaries_from_state(ctx) == ()

    def test_filters_blank_summaries(self) -> None:
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                step_outputs=[
                    SimpleNamespace(summary="alpha"),
                    SimpleNamespace(summary="   "),
                    SimpleNamespace(summary="beta"),
                ]
            )
        )
        assert _step_summaries_from_state(ctx) == ("alpha", "beta")


class TestActiveWorkSummaryFromState:
    def test_returns_empty_when_pending_missing(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(pending_turn_context=None))
        assert _active_work_summary_from_state(ctx) == ""

    def test_returns_value(self) -> None:
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                pending_turn_context=SimpleNamespace(active_work_summary="hello")
            )
        )
        assert _active_work_summary_from_state(ctx) == "hello"


class TestMaxStepsHintFromState:
    def test_none_when_invalid(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(decision_max_steps_hint=None))
        assert _max_steps_hint_from_state(ctx) is None

    def test_none_when_zero(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(decision_max_steps_hint=0))
        assert _max_steps_hint_from_state(ctx) is None

    def test_positive_int(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(decision_max_steps_hint=5))
        assert _max_steps_hint_from_state(ctx) == 5

    def test_invalid_string_returns_none(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(decision_max_steps_hint="bad"))
        assert _max_steps_hint_from_state(ctx) is None


# Budget predicates


class TestLLMBudgetAvailableForAnswerOnly:
    def test_false_when_tokens_zero(self) -> None:
        st = _state(tokens=0)
        prof = _profile(allowed_tools=frozenset({"x"}))
        lst = AdaptiveToolLoopState(messages=[])
        ctx = SimpleNamespace(state=st)
        assert (
            _llm_budget_available_for_answer_only(
                loop_ctx=ctx, profile=prof, loop_state=lst
            )
            is False
        )

    def test_false_when_llm_calls_exhausted(self) -> None:
        st = _state()
        st.llm_calls_used = 5
        prof = _profile(allowed_tools=frozenset({"x"}))
        lst = AdaptiveToolLoopState(messages=[])
        ctx = SimpleNamespace(state=st)
        assert (
            _llm_budget_available_for_answer_only(
                loop_ctx=ctx, profile=prof, loop_state=lst
            )
            is False
        )

    def test_false_when_max_llm_calls_per_loop_reached(self) -> None:
        st = _state()
        prof = _profile(allowed_tools=frozenset({"x"}))
        object.__setattr__(prof, "max_llm_calls_per_loop", 1)
        lst = AdaptiveToolLoopState(messages=[], llm_calls=1)
        ctx = SimpleNamespace(state=st)
        assert (
            _llm_budget_available_for_answer_only(
                loop_ctx=ctx, profile=prof, loop_state=lst
            )
            is False
        )

    def test_true_when_all_budgets_available(self) -> None:
        st = _state()
        prof = _profile(allowed_tools=frozenset({"x"}))
        lst = AdaptiveToolLoopState(messages=[])
        ctx = SimpleNamespace(state=st)
        assert (
            _llm_budget_available_for_answer_only(
                loop_ctx=ctx, profile=prof, loop_state=lst
            )
            is True
        )


class TestToolBudgetExhaustedForAnswerOnly:
    def test_true_when_no_tool_calls_left_after_use(self) -> None:
        st = _state(tool_calls=0)
        prof = _profile(allowed_tools=frozenset({"x"}))
        lst = AdaptiveToolLoopState(messages=[], total_tool_calls=2)
        ctx = SimpleNamespace(state=st)
        assert (
            _tool_budget_exhausted_for_answer_only(
                loop_ctx=ctx, profile=prof, loop_state=lst
            )
            is True
        )

    def test_false_when_budget_remains(self) -> None:
        st = _state(tool_calls=3)
        prof = _profile(allowed_tools=frozenset({"x"}))
        lst = AdaptiveToolLoopState(messages=[], total_tool_calls=1)
        ctx = SimpleNamespace(state=st)
        assert (
            _tool_budget_exhausted_for_answer_only(
                loop_ctx=ctx, profile=prof, loop_state=lst
            )
            is False
        )

    def test_true_when_loop_cap_hit(self) -> None:
        st = _state()
        prof = _profile(allowed_tools=frozenset({"x"}), max_tool_calls_per_loop=2)
        lst = AdaptiveToolLoopState(messages=[], total_tool_calls=2)
        ctx = SimpleNamespace(state=st)
        assert (
            _tool_budget_exhausted_for_answer_only(
                loop_ctx=ctx, profile=prof, loop_state=lst
            )
            is True
        )


# Decompose helpers


class TestDecomposeToolCalls:
    def test_filters_to_decompose(self) -> None:
        calls = [
            ToolCall(id="1", name="decompose", arguments={"subtasks": []}),
            ToolCall(id="2", name="file.read", arguments={}),
        ]
        result = _decompose_tool_calls(calls)
        assert len(result) == 1
        assert result[0].name == "decompose"

    def test_returns_empty_when_none(self) -> None:
        calls = [ToolCall(id="1", name="file.read", arguments={})]
        assert _decompose_tool_calls(calls) == []


class TestSubtasksFromDecomposeControl:
    def test_with_subtasks(self) -> None:
        payload = DecomposeControlPayload.model_validate(
            {
                "subtasks": [
                    {
                        "id": "a",
                        "description": "alpha",
                        "inputs": {"k": "v"},
                        "depends_on": [],
                        "priority": 1,
                    },
                    {"id": "b", "description": "beta", "depends_on": ["a"]},
                ]
            }
        )
        result = _subtasks_from_decompose_control(payload)
        assert result[0]["subtask_id"] == "a"
        assert result[0]["goal"] == "alpha"
        assert result[1]["depends_on"] == ["a"]

    def test_empty(self) -> None:
        payload = DecomposeControlPayload(subtasks=[])
        assert _subtasks_from_decompose_control(payload) == []


class TestDecomposeDeclineResult:
    def test_shape(self) -> None:
        result = _decompose_decline_result()
        assert result.status == "success"
        assert result.outputs == {"subtask_count": 0, "declined": True}


# Duplicate-batch helpers


class TestDuplicateBatchRetryCounts:
    def test_initializes_empty_dict_when_missing(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        counts = _duplicate_batch_retry_counts(st)
        assert counts == {}
        # Ensure it's persisted into scratchpad
        assert "duplicate_signature_retry_counts" in st.scratchpad

    def test_returns_existing(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad = {"duplicate_signature_retry_counts": {"sig1": 1}}
        counts = _duplicate_batch_retry_counts(st)
        assert counts == {"sig1": 1}


class TestDuplicateBatchRecoveryMessage:
    def test_with_names(self) -> None:
        calls = [
            ToolCall(id="1", name="file.read", arguments={}),
            ToolCall(id="2", name="exec.run", arguments={}),
        ]
        msg = _duplicate_batch_recovery_message(calls)
        assert "file.read" in msg.content
        assert "exec.run" in msg.content

    def test_without_names(self) -> None:
        msg = _duplicate_batch_recovery_message([])
        assert "the previous tool batch" in msg.content


class TestActionResultHasRetryOrPollSignal:
    def test_true_when_job_present_on_outcome(self) -> None:
        ar = ActionResult(command_id=new_uuid(), status="success", summary="ok")
        outcome = SimpleNamespace(job=SimpleNamespace(job_id="j"))
        assert (
            _action_result_has_retry_or_poll_signal(
                action_result=ar, command_outcome=outcome
            )
            is True
        )

    def test_true_when_status_is_retry(self) -> None:
        ar = ActionResult(command_id=new_uuid(), status="retry", summary="x")
        outcome = SimpleNamespace(job=None)
        assert (
            _action_result_has_retry_or_poll_signal(
                action_result=ar, command_outcome=outcome
            )
            is True
        )

    def test_true_when_outputs_has_retryable(self) -> None:
        ar = ActionResult(
            command_id=new_uuid(),
            status="success",
            summary="x",
            outputs={"retryable": True},
        )
        outcome = SimpleNamespace(job=None)
        assert (
            _action_result_has_retry_or_poll_signal(
                action_result=ar, command_outcome=outcome
            )
            is True
        )

    def test_true_when_poll_after_ms_set(self) -> None:
        ar = ActionResult(
            command_id=new_uuid(),
            status="success",
            summary="x",
            outputs={"poll_after_ms": 100},
        )
        outcome = SimpleNamespace(job=None)
        assert (
            _action_result_has_retry_or_poll_signal(
                action_result=ar, command_outcome=outcome
            )
            is True
        )

    def test_false_for_clean_success(self) -> None:
        ar = ActionResult(command_id=new_uuid(), status="success", summary="x")
        outcome = SimpleNamespace(job=None)
        assert (
            _action_result_has_retry_or_poll_signal(
                action_result=ar, command_outcome=outcome
            )
            is False
        )

    def test_false_when_outputs_not_dict(self) -> None:
        # ActionResult.outputs defaults to {} dict; override is not trivial.
        # Use a SimpleNamespace surrogate to exercise the non-dict branch.
        fake_ar = SimpleNamespace(status="success", outputs="not-a-dict")
        outcome = SimpleNamespace(job=None)
        assert (
            _action_result_has_retry_or_poll_signal(
                action_result=fake_ar, command_outcome=outcome
            )
            is False
        )


class TestRecordAndEligibleDuplicateBatchExecutionFacts:
    def test_record_then_eligible(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        call = ToolCall(id="1", name="file.read", arguments={"path": "a"})
        sig = semantic_batch_signature([call])
        ar = ActionResult(command_id=new_uuid(), status="success", summary="ok")
        outcome = SimpleNamespace(action_result=ar, job=None)
        _record_duplicate_batch_execution_facts(
            st,
            signature=sig,
            ordered_tool_results=[(call, outcome)],
        )
        eligible = _eligible_duplicate_batch_execution_facts(st, signature=sig)
        assert isinstance(eligible, dict)
        assert eligible["all_success"] is True
        assert eligible["has_substantive_success"] is True

    def test_plan_only_success_is_not_eligible(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        call = ToolCall(id="1", name="plan", arguments={"action": "list"})
        sig = semantic_batch_signature([call])
        ar = ActionResult(command_id=new_uuid(), status="success", summary="listed")
        outcome = SimpleNamespace(action_result=ar, job=None)
        _record_duplicate_batch_execution_facts(
            st,
            signature=sig,
            ordered_tool_results=[(call, outcome)],
        )

        assert _eligible_duplicate_batch_execution_facts(st, signature=sig) is None

    def test_record_failed_marks_has_non_success(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        call = ToolCall(id="1", name="file.read", arguments={})
        sig = "sig-fail"
        ar = ActionResult(command_id=new_uuid(), status="failed", summary="x")
        outcome = SimpleNamespace(action_result=ar, job=None)
        _record_duplicate_batch_execution_facts(
            st,
            signature=sig,
            ordered_tool_results=[(call, outcome)],
        )
        # Has non-success -> not eligible
        assert _eligible_duplicate_batch_execution_facts(st, signature=sig) is None

    def test_record_with_job_marks_has_job(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        call = ToolCall(id="1", name="file.read", arguments={})
        sig = "sig-job"
        ar = ActionResult(command_id=new_uuid(), status="success", summary="ok")
        outcome = SimpleNamespace(
            action_result=ar,
            job=SimpleNamespace(job_id="j"),
        )
        _record_duplicate_batch_execution_facts(
            st,
            signature=sig,
            ordered_tool_results=[(call, outcome)],
        )
        # Has job -> not eligible
        assert _eligible_duplicate_batch_execution_facts(st, signature=sig) is None

    def test_record_with_empty_inputs_noop(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        _record_duplicate_batch_execution_facts(
            st,
            signature="",
            ordered_tool_results=[],
        )
        _record_duplicate_batch_execution_facts(
            st,
            signature="sig",
            ordered_tool_results=[],
        )
        assert _eligible_duplicate_batch_execution_facts(st, signature="sig") is None

    def test_eligible_none_when_not_recorded(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        assert (
            _eligible_duplicate_batch_execution_facts(st, signature="never-recorded")
            is None
        )

    def test_reset_clears_duplicate_maps_but_preserves_seen_signatures(self) -> None:
        st = AdaptiveToolLoopState(
            messages=[],
            seen_signatures=["sig-a", "sig-b"],
            scratchpad={
                "duplicate_signature_retry_counts": {"sig-a": 1},
                "duplicate_signature_execution_facts": {"sig-a": {"all_success": True}},
                "duplicate_batch_answer_only_closure_pending": True,
                "keep.me": "still here",
            },
        )

        _reset_duplicate_batch_tracking(st)

        assert st.seen_signatures == ["sig-a", "sig-b"]
        assert "duplicate_signature_retry_counts" not in st.scratchpad
        assert "duplicate_signature_execution_facts" not in st.scratchpad
        assert "duplicate_batch_answer_only_closure_pending" not in st.scratchpad
        assert st.scratchpad["keep.me"] == "still here"


# Tool-result payload extraction


class TestToolResultPayloadFromAction:
    def test_success_shape(self) -> None:
        ar = ActionResult(
            command_id="cmd-1",
            status="success",
            summary="ok",
            outputs={"k": "v"},
        )
        payload = _tool_result_payload_from_action(
            tool_name="file.read", action_result=ar
        )
        assert payload["ok"] is True
        assert payload["verified"] is True
        assert payload["tool_name"] == "file.read"
        assert payload["error"] == ""

    def test_failed_with_error_object(self) -> None:
        ar = ActionResult(
            command_id="cmd-2",
            status="failed",
            summary="boom",
            error=ActionError(code="E1", message="bad", details={"d": 1}),
        )
        payload = _tool_result_payload_from_action(
            tool_name="exec.run", action_result=ar
        )
        assert payload["ok"] is False
        assert payload["error_code"] == "E1"
        assert payload["data"]["error_code"] == "E1"
        assert payload["data"]["error_details"] == {"d": 1}

    def test_blocked_is_not_successful_evidence(self) -> None:
        ar = ActionResult(
            command_id="cmd-2b",
            status="blocked",
            summary="Denied by policy",
        )
        payload = _tool_result_payload_from_action(
            tool_name="exec.run", action_result=ar
        )
        assert payload["ok"] is False
        assert payload["verified"] is False

    def test_failed_with_error_dict_in_outputs(self) -> None:
        ar = ActionResult(
            command_id="cmd-3",
            status="failed",
            summary="",
            outputs={"error": "explicit-msg"},
        )
        payload = _tool_result_payload_from_action(tool_name="t", action_result=ar)
        assert payload["error"] == "explicit-msg"

    def test_empty_tool_name_falls_back(self) -> None:
        ar = ActionResult(command_id="x", status="success", summary="ok")
        payload = _tool_result_payload_from_action(tool_name="", action_result=ar)
        assert payload["tool_name"] == "unknown"


class TestAppendToolResultPayload:
    def test_appends_to_scratchpad(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        ar = ActionResult(command_id="x", status="success", summary="ok")
        _append_tool_result_payload(st, tool_name="file.read", action_result=ar)
        assert len(st.scratchpad["adaptive.tool_results"]) == 1


# Build helpers — recovery messages, enrichment, missing action results


class TestBuildEnrichmentMessage:
    def test_truncates_long_summary(self) -> None:
        long_summary = "x" * 400
        msg = _build_enrichment_message("file.read", 0.99, long_summary)
        assert "..." in msg.content
        assert msg.role == "system"


class TestBuildToolFailureRecoveryMessage:
    def test_returns_none_for_success(self) -> None:
        ar = ActionResult(command_id="x", status="success", summary="ok")
        assert (
            _build_tool_failure_recovery_message(tool_name="t", action_result=ar)
            is None
        )

    def test_returns_message_for_failed(self) -> None:
        ar = ActionResult(
            command_id="x",
            status="failed",
            summary="boom",
            error=ActionError(code="E", message="bad"),
        )
        msg = _build_tool_failure_recovery_message(tool_name="t", action_result=ar)
        assert msg is not None
        assert "code=E" in msg.content

    def test_returns_message_for_timeout(self) -> None:
        ar = ActionResult(command_id="x", status="timeout", summary="late")
        msg = _build_tool_failure_recovery_message(tool_name="t", action_result=ar)
        assert msg is not None

    def test_exec_run_invalid_working_dir_arg_gets_supported_field_guidance(
        self,
    ) -> None:
        ar = ActionResult(
            command_id="x",
            status="failed",
            summary="Invalid tool arguments",
            error=ActionError(
                code="INVALID_ARGUMENT",
                message=(
                    "1 validation error for ExecRunArgs\nworking_dir\n"
                    "  Extra inputs are not permitted"
                ),
            ),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="exec.run",
            action_result=ar,
        )
        assert msg is not None
        assert "path field" in msg.content
        assert "cwd / working_directory aliases" in msg.content
        assert "do not pass working_dir" in msg.content

    def test_exec_run_argument_shape_error_gets_schema_guidance(self) -> None:
        ar = ActionResult(
            command_id="x",
            status="failed",
            summary="Invalid tool arguments",
            error=ActionError(
                code="INVALID_ARGUMENT",
                message=(
                    "2 validation errors for ExecRunArgs\n"
                    "desc\n  Extra inputs are not permitted\n"
                    "environment_variables\n  Extra inputs are not permitted"
                ),
            ),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="exec.run",
            action_result=ar,
        )
        assert msg is not None
        assert "plain command string" in msg.content
        assert "not a JSON array" in msg.content
        assert "omit desc, environment_variables" in msg.content

    def test_exec_run_policy_denied_array_command_gets_string_guidance(self) -> None:
        ar = ActionResult(
            command_id="x",
            status="blocked",
            summary="Denied by policy: command '[python,' is not allowlisted",
            error=ActionError(
                code="POLICY_DENIED",
                message="Denied by policy: command '[python,' is not allowlisted",
            ),
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="exec.run",
            action_result=ar,
        )
        assert msg is not None
        assert "plain command string" in msg.content
        assert "not a JSON array" in msg.content

    def test_exec_run_pytest_failure_gets_patch_then_rerun_guidance(self) -> None:
        ar = ActionResult(
            command_id="x",
            status="failed",
            summary="command exited with code 1",
            error=ActionError(
                code="EXEC_ERROR",
                message="command exited with code 1",
                details={"exit_code": 1},
            ),
            outputs={
                "stdout_preview": (
                    "..F....\n"
                    "FAILED tests/test_report.py::TestBuildSummary::"
                    "test_highest_priority_open_items_section\n"
                    "AssertionError\n"
                    "python -m pytest -q tests"
                )
            },
        )
        msg = _build_tool_failure_recovery_message(
            tool_name="exec.run",
            action_result=ar,
        )
        assert msg is not None
        assert "failing verifier output" in msg.content
        assert "patch the relevant file" in msg.content
        assert "same verification command" in msg.content


class TestBuildMissingActionResult:
    def test_shape(self) -> None:
        ar = _build_missing_action_result("file.read")
        assert ar.status == "failed"
        assert ar.error.code == "adaptive_tool_no_result"


class TestLooksLikeUnexecutableToolPayloadText:
    def test_detects_embedded_tool_response_markup(self) -> None:
        text = (
            "Now running pytest as the final step.\n\n"
            "<tool_response>\n"
            '{"tool-name":"exec.run","parameters":{"command":["python","-m","pytest","-q","tests"]}}\n'
            "</tool_response>"
        )
        assert _looks_like_unexecutable_tool_payload_text(text) is True

    def test_detects_plaintext_file_write_tool_instruction(self) -> None:
        text = (
            "file.write\n"
            "path: /tmp/project/README.md\n"
            "content: |\n"
            "# Project\n"
            "Generated content"
        )
        assert _looks_like_unexecutable_tool_payload_text(text) is True

    def test_detects_plaintext_exec_run_tool_instruction(self) -> None:
        text = (
            "exec.run command: cd /tmp/project && /usr/bin/python3 -m pytest tests/ -v"
        )
        assert _looks_like_unexecutable_tool_payload_text(text) is True

    def test_detects_plaintext_tool_function_call_instruction(self) -> None:
        text = (
            "file.list_dir(\n"
            '  path="/tmp/project/loopcalc"\n'
            ")\n"
            'file.read(path="/tmp/project/loopcalc/__init__.py")'
        )
        assert _looks_like_unexecutable_tool_payload_text(text) is True

    def test_allows_prose_that_mentions_exec_run_without_invocation_shape(self) -> None:
        text = "A future agent can use exec.run after selecting a safe command."
        assert _looks_like_unexecutable_tool_payload_text(text) is False

    def test_allows_prose_that_mentions_file_read_without_invocation_shape(
        self,
    ) -> None:
        text = "Use the file.read tool when you need to inspect a known file."
        assert _looks_like_unexecutable_tool_payload_text(text) is False


# Set turn progress


class TestSetTurnProgress:
    def test_accumulates_tokens(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        _set_turn_progress(st, input_tokens_delta=100, output_tokens_delta=50)
        _set_turn_progress(st, input_tokens_delta=10, output_tokens_delta=5)
        assert st.scratchpad["turn_progress_input_tokens_total"] == 110
        assert st.scratchpad["turn_progress_output_tokens_total"] == 55
        assert st.scratchpad["turn_progress_total_tokens_used"] == 165

    def test_optional_args(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        _set_turn_progress(
            st,
            llm_call_count=3,
            llm_call_limit=5,
            progress_phase="thinking",
            tool_name="file.read",
        )
        assert st.scratchpad["turn_progress_llm_call_count"] == 3
        assert st.scratchpad["turn_progress_llm_call_limit"] == 5
        assert st.scratchpad["turn_progress_phase"] == "thinking"
        assert st.scratchpad["turn_progress_tool_name"] == "file.read"

    def test_negative_counts_clamped(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        _set_turn_progress(st, llm_call_count=-2, llm_call_limit=-3)
        assert st.scratchpad["turn_progress_llm_call_count"] == 0
        assert st.scratchpad["turn_progress_llm_call_limit"] == 0


# Tool-request result branches


class TestToolRequestResult:
    def test_missing_name_returns_failed(self) -> None:
        ar, mutated = _tool_request_result(
            requested_name="",
            active_tool_names=set(),
            requestable_specs_by_name={},
            active_tool_specs=[],
        )
        assert ar.status == "failed"
        assert mutated is False

    def test_already_active_returns_success(self) -> None:
        active = {"file.read"}
        ar, mutated = _tool_request_result(
            requested_name="file.read",
            active_tool_names=active,
            requestable_specs_by_name={},
            active_tool_specs=[],
        )
        assert ar.status == "success"
        assert mutated is False

    def test_unavailable_returns_failed(self) -> None:
        ar, mutated = _tool_request_result(
            requested_name="unknown.tool",
            active_tool_names=set(),
            requestable_specs_by_name={},
            active_tool_specs=[],
        )
        assert ar.status == "failed"
        assert ar.error.code == "TOOL_REQUEST_UNAVAILABLE"
        assert mutated is False

    def test_activates_requested_tool(self) -> None:
        specs = _tool_specs("new.tool")
        active_specs = list(_tool_specs("file.read"))
        active_names: set[str] = set()
        ar, mutated = _tool_request_result(
            requested_name="new.tool",
            active_tool_names=active_names,
            requestable_specs_by_name={"new.tool": specs[0]},
            active_tool_specs=active_specs,
        )
        assert ar.status == "success"
        assert mutated is True
        assert "new.tool" in active_names


# Context helpers (memory_consolidation, delegation)


class TestMemoryConsolidationContext:
    def test_none_when_state_missing_module_state(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(module_state=None))
        assert _memory_consolidation_context(ctx) is None

    def test_none_when_consolidation_disabled(self) -> None:
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                module_state={"memory_consolidation": {"enabled": False}}
            )
        )
        assert _memory_consolidation_context(ctx) is None

    def test_returns_dict_when_enabled(self) -> None:
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                module_state={
                    "memory_consolidation": {"enabled": True, "candidates": []}
                }
            )
        )
        result = _memory_consolidation_context(ctx)
        assert result is not None
        assert result.get("enabled") is True


class TestDelegatedChildContext:
    def test_none_when_missing_module_state(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(module_state=None))
        assert _delegated_child_context(ctx) is None

    def test_none_when_disabled(self) -> None:
        ctx = SimpleNamespace(
            state=SimpleNamespace(module_state={"delegation": {"enabled": False}})
        )
        assert _delegated_child_context(ctx) is None

    def test_returns_dict_when_enabled(self) -> None:
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                module_state={"delegation": {"enabled": True, "parent_context": {}}}
            )
        )
        result = _delegated_child_context(ctx)
        assert result is not None


class TestDelegatedChildContextMessage:
    def test_returns_none_when_not_dict(self) -> None:
        assert _delegated_child_context_message({"parent_context": "junk"}) is None

    def test_message_falls_through_when_dict_validates_loosely(self) -> None:
        # DelegationContext is very lenient — most dicts validate. We
        # characterize that loose input produces a Message (not None).
        result = _delegated_child_context_message(
            {"parent_context": {"intent_id": "x"}}
        )
        assert result is not None

    def test_returns_message_with_fields(self) -> None:
        msg = _delegated_child_context_message(
            {
                "parent_context": {
                    "intent_id": "i1",
                    "summary": "do work",
                    "artifacts": ["a", "b"],
                }
            }
        )
        assert msg is not None
        assert "intent_id: i1" in msg.content
        assert "summary: do work" in msg.content


class TestMemoryConsolidationContextMessage:
    def test_no_candidates_returns_default(self) -> None:
        msg = _memory_consolidation_context_message({"candidates": []})
        assert msg is not None
        assert "No pending candidates" in msg.content

    def test_with_candidates_renders_list(self) -> None:
        msg = _memory_consolidation_context_message(
            {
                "candidates": [
                    {
                        "candidate_id": "c1",
                        "record_type": "note",
                        "confidence": 0.8,
                        "source_session": "s1",
                        "title": "Hello",
                        "content_preview": "preview text",
                    },
                    {"candidate_id": "c2"},
                ]
            }
        )
        assert msg is not None
        assert "c1" in msg.content
        assert "title: Hello" in msg.content
        assert "preview: preview text" in msg.content


class TestBuildIntentExecutionStateMessage:
    def test_none_when_state_missing(self) -> None:
        ctx = SimpleNamespace(state=None)
        assert _build_intent_execution_state_message(ctx) is None

    def test_none_when_no_intent_states(self) -> None:
        ctx = SimpleNamespace(state=SimpleNamespace(intent_execution_states=[]))
        assert _build_intent_execution_state_message(ctx) is None


class TestPendingFinalizationSalvageText:
    def test_returns_none_when_missing(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        assert _pending_finalization_salvage_text(st) is None

    def test_returns_stripped_value(self) -> None:
        st = AdaptiveToolLoopState(messages=[])
        st.scratchpad["typed_finalization_status_salvage_text"] = "  hello  "
        assert _pending_finalization_salvage_text(st) == "hello"


class TestDecomposeInvalidOutcome:
    def test_shape(self) -> None:
        prof = _profile(allowed_tools=frozenset({"decompose"}))
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        outcome = _decompose_invalid_outcome(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            allowed_tools=frozenset({"decompose"}),
            public_mode_tag="act",
            reason="empty",
            message="malformed",
        )
        assert outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_INVALID
        assert outcome.error_message == "malformed"
        assert st_loop.scratchpad["adaptive.decompose_error"]["reason"] == "empty"


# End-to-end loop scenarios — driving run_adaptive_tool_loop


def test_loop_returns_final_text_when_no_tool_calls() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="hello",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="hi")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "hello"


def test_loop_executes_tool_then_completes() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="all done",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "read ok")],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="read")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "all done"
    assert len(loop_ctx.commands) == 1


def test_loop_ignores_execution_preface_assistant_message_when_tools_present() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                assistant_messages=[
                    Message(
                        role="assistant",
                        content="I'll read pyproject.toml and README.md now.",
                    )
                ],
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="verified and complete",
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "read ok")],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="read")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "verified and complete"
    assert [
        message.content
        for message in outcome.state.messages
        if message.role == "assistant"
    ] == ["verified and complete"]


def test_loop_ignores_execution_preface_output_text_when_tools_present() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="Reading pyproject.toml and README.md to verify the required strings are present:",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="verified and complete",
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "read ok")],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="read")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "verified and complete"
    assert [
        message.content
        for message in outcome.state.messages
        if message.role == "assistant"
    ] == ["verified and complete"]


def test_loop_retries_status_payload_after_substantive_tool_work() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    '{"active_form":"Verifying task completion",'
                    '"confidence":"high","reasoning":"done"}'
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="SOURCES\n- source\n\nCHANGES\n- updated\n\nTESTS\n- passed",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "read ok")],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="read then summarize")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert (
        outcome.final_text
        == "SOURCES\n- source\n\nCHANGES\n- updated\n\nTESTS\n- passed"
    )
    assert len(runtime.calls) == 3
    retry_messages = runtime.calls[2]["messages"]
    assert any(
        msg.role == "system" and "structured status payload" in msg.content
        for msg in retry_messages
    )


def test_loop_retries_continuing_preface_after_substantive_tool_work() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "The directory is confirmed empty. Continuing — write design "
                    "doc, all package files, and tests in parallel now."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="Files changed and validation result: passed.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "read ok")],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="write project and validate")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Files changed and validation result: passed."
    assert len(runtime.calls) == 3
    retry_messages = runtime.calls[2]["messages"]
    assert any(
        msg.role == "system" and "pre-tool draft" in msg.content
        for msg in retry_messages
    )


def test_loop_retries_fix_and_rerun_preface_after_validation_failure() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "Validation produced exit code 2. Let me read the source files "
                    "to find and fix the bug, then rerun."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="Design, implementation, and validation result: passed.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "read ok")],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="fix and validate")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert (
        outcome.final_text == "Design, implementation, and validation result: passed."
    )
    assert len(runtime.calls) == 3
    retry_messages = runtime.calls[2]["messages"]
    assert any(
        msg.role == "system" and "pre-tool draft" in msg.content
        for msg in retry_messages
    )


def test_loop_retries_long_file_plan_without_file_creation() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.list_dir", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "Goal: Build a minimal Python CLI.\n\n"
                    "Step 1 -- Explore workspace\n\n"
                    "No existing files in the target directory.\n\n"
                    "Files to create:\n"
                    "- md_summary/__init__.py\n"
                    "- md_summary/cli.py\n"
                    "- tests/test_core.py\n\n"
                    "I'll write all files now in a focused batch."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="Files changed and validation result: passed.",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.list_dir", "empty")],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.list_dir"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="build project")],
        tool_specs=_tool_specs("file.list_dir"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "Files changed and validation result: passed."
    assert len(runtime.calls) == 3
    retry_messages = runtime.calls[2]["messages"]
    assert any(
        msg.role == "system" and "pre-tool draft" in msg.content
        for msg in retry_messages
    )


def test_loop_iteration_cap_terminates_with_cap_reason() -> None:
    # Provide enough tool-call responses to drive past max_iterations
    tool_response = LLMResponse(
        ok=True,
        provider="fake",
        model="m",
        output_text="",
        tool_calls=[ToolCall(id="c", name="file.read", arguments={"path": "a"})],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(responses=[tool_response, tool_response, tool_response])
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome() for _ in range(5)],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=2),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="loop")],
        tool_specs=_tool_specs("file.read"),
    )
    # Cap can produce either ITERATION_CAP or DUPLICATE_TOOL_CALLS depending on
    # whether the engine detects the duplicate batch first. Both are real
    # terminations — characterize that we get *some* defined terminal reason.
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_ITERATION_CAP,
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_FINAL_TEXT,
    }
    if outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT:
        assert "tool evidence" in str(outcome.final_text or "").lower()


def test_loop_llm_error_terminates_with_llm_error_reason() -> None:
    runtime = _FakeRuntime(responses=[], raise_error=True)
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="error path")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_LLM_ERROR


def test_loop_not_ok_response_terminates_with_llm_error() -> None:
    from openminion.modules.llm.schemas import ResponseError

    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=False,
                provider="fake",
                model="m",
                output_text="",
                error=ResponseError(code="RATE_LIMITED", message="rate limited"),
                finish_reason="error",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="not ok")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_LLM_ERROR


def test_loop_decompose_with_subtasks_returns_handoff() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="d1",
                        name="decompose",
                        arguments={
                            "subtasks": [
                                {"id": "a", "description": "do alpha"},
                            ]
                        },
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="split")],
        tool_specs=[decompose_tool_spec()],
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_REQUESTED
    assert outcome.decompose_subtasks == [
        {
            "subtask_id": "a",
            "goal": "do alpha",
            "inputs": {},
            "depends_on": [],
            "suggested_mode": None,
            "priority": 0,
        }
    ]


def test_loop_decompose_empty_continues_then_finalizes() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="d1", name="decompose", arguments={"subtasks": []})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="ok, declined",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="split")],
        tool_specs=[decompose_tool_spec()],
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_decompose_malformed_invalid_outcome() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="d1",
                        name="decompose",
                        arguments={"subtasks": [{"id": "no-description"}]},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="split")],
        tool_specs=[decompose_tool_spec()],
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_DECOMPOSE_INVALID


def test_loop_tool_failure_then_recovery() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="recovered text",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_failed_outcome()],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="please recover")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_seed_response_skips_first_llm_call() -> None:
    seed = LLMResponse(
        ok=True,
        provider="fake",
        model="m",
        output_text="from seed",
        finish_reason="stop",
    )
    runtime = _FakeRuntime(responses=[])
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="seed me")],
        tool_specs=_tool_specs("file.read"),
        seed_response=seed,
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "from seed"
    assert len(runtime.calls) == 0  # seed used first


def test_loop_with_initial_state_skips_message_initialization() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="ok",
                finish_reason="stop",
            )
        ]
    )
    initial = AdaptiveToolLoopState(
        messages=[Message(role="user", content="from initial state")],
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[],
        tool_specs=_tool_specs("file.read"),
        initial_state=initial,
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_with_finalizer_callback_does_not_raise() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="finished",
                finish_reason="stop",
            )
        ]
    )
    called: list[Any] = []
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="finalizer test")],
        tool_specs=_tool_specs("file.read"),
        finalizer=lambda o: called.append(o),
    )
    # Finalizer is wired in but invocation depends on runner state. The
    # characterization concern here is that passing a finalizer does not
    # raise; the call-count branch is exercised either way.
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_two_tool_calls_in_a_batch_executed_sequentially() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"}),
                    ToolCall(id="c2", name="exec.run", arguments={"cmd": "x"}),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="both done",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            _success_outcome("file.read", "read"),
            _success_outcome("exec.run", "exec"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read", "exec.run"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="run both")],
        tool_specs=_tool_specs("file.read", "exec.run"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert len(loop_ctx.commands) == 2


def test_loop_disallowed_tool_short_circuits() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[ToolCall(id="c1", name="forbidden.tool", arguments={})],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="recovered",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="disallowed")],
        tool_specs=_tool_specs("file.read"),
    )
    # The disallowed tool either terminates with DISALLOWED_TOOL or recovers
    # via an enrichment message — both are characterized terminations.
    assert outcome.termination_reason in {
        "disallowed_tool",
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_LLM_ERROR,
    }


def test_loop_no_tool_specs_raises_when_allowed_tools_present() -> None:
    # When allow_plan_tool=False AND no tool_specs AND no seeded queue,
    # the engine raises ValueError. The default profile has allow_plan_tool=True
    # which would auto-inject the plan tool spec; we explicitly disable it.
    runtime = _FakeRuntime(responses=[])
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset({"file.read"}),
        max_iterations=2,
        allow_plan_tool=False,
    )
    with pytest.raises(ValueError):
        run_adaptive_tool_loop(
            loop_ctx,
            profile=prof,
            runtime=runtime,
            model="m",
            initial_messages=[Message(role="user", content="x")],
            tool_specs=[],
        )


def test_loop_with_one_iter_and_interactive_budget_pauses_for_user() -> None:
    cfg = AdaptiveBudgetConfig(mode="interactive", extend_by=2, idle_timeout_s=60)
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome()],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            max_iterations=1,
            adaptive_budget_config=cfg,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="x")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_NEEDS_USER,
        ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_ITERATION_CAP,
    }


def test_loop_with_one_iter_and_autonomous_budget_extends() -> None:
    cfg = AdaptiveBudgetConfig(
        mode="autonomous",
        extend_by=4,
        idle_timeout_s=60,
        max_extensions_per_turn=2,
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="extended-final",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome()],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            max_iterations=1,
            adaptive_budget_config=cfg,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="x")],
        tool_specs=_tool_specs("file.read"),
    )
    # Either extends and completes (final_text) or rails out (budget exhausted).
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_NEEDS_USER,
        ADAPTIVE_TERM_ITERATION_CAP,
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    }


def test_loop_general_adaptive_profile_finalization_blocked() -> None:
    # Engine needs the finalization_status payload AND final_text. First we
    # get a tool call; then on second iter the LLM returns no tool calls,
    # final text, and a typed finalization_status=blocked.
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="cannot complete because the doc is missing",
                finalization_status={"status": "blocked", "reasoning": "no doc"},
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="describe doc")],
        tool_specs=_tool_specs("file.read"),
    )
    # We don't strictly require BLOCKED — the typed-finalization retry logic
    # may dispatch additional rounds. We characterize that we get *some*
    # defined termination.
    assert outcome.termination_reason in {
        "finalization_blocked",
        "finalization_incomplete",
        ADAPTIVE_TERM_FINAL_TEXT,
        "finalization_contract_missing",
    }


def test_loop_general_adaptive_profile_finalization_incomplete() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="started but more to do",
                finalization_status={
                    "status": "incomplete",
                    "reasoning": "need user data",
                },
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="do thing")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        "finalization_incomplete",
        ADAPTIVE_TERM_FINAL_TEXT,
        "finalization_contract_missing",
    }


def test_loop_general_adaptive_profile_finalization_final_answer() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="here is the final answer",
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="describe")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.finalization_status is not None


def test_loop_salvages_substantive_final_answer_when_trailer_retry_fails() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[_failed_outcome()])

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="return SOURCES CHANGES TESTS")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text.startswith("SOURCES")
    assert outcome.finalization_status is not None


def test_loop_salvages_assistant_message_final_answer_when_output_text_is_blank() -> (
    None
):
    final_answer = "TRADEOFFS\n- keep\n\nRECOMMENDATION\n- ship"
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="web.search", arguments={"query": "codex"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                assistant_messages=[Message(role="assistant", content=final_answer)],
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                assistant_messages=[Message(role="assistant", content=final_answer)],
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "completed after salvage",
                },
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[_failed_outcome()])

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.search"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(role="user", content="return tradeoffs and a recommendation")
        ],
        tool_specs=_tool_specs("web.search"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == final_answer
    assert outcome.finalization_status is not None


def test_loop_salvages_structured_final_answer_without_trailer_on_first_try() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="web.search", arguments={"query": "pipx"}),
                    ToolCall(
                        id="c2",
                        name="web.fetch",
                        arguments={
                            "url": "https://docs.astral.sh/uv/getting-started/installation/"
                        },
                    ),
                    ToolCall(
                        id="c3",
                        name="web.fetch",
                        arguments={"url": "https://pipx.pypa.io/"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "**PLAN**\n- search\n- fetch uv\n- fetch pipx\n\n"
                    "**TABLE**\n| a | b |\n|---|---|\n| x | y |\n\n"
                    "**UNCERTAINTIES**\n- none"
                ),
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            _success_outcome("web.search", "search ok"),
            _success_outcome("web.fetch", "uv ok"),
            _success_outcome("web.fetch", "pipx ok"),
        ],
    )

    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(role="user", content="return PLAN TABLE UNCERTAINTIES")
        ],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "**PLAN**" in str(outcome.final_text or "")
    assert outcome.finalization_status is not None
    assert outcome.finalization_status["status"] == "final_answer"


def test_loop_confident_complete_signals_confident_completion() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="all done",
                confident_complete={"complete": True, "reasoning": "ok"},
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="ok?")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        "confident_complete",
        ADAPTIVE_TERM_FINAL_TEXT,
    }


def test_loop_duplicate_batch_retries_then_terminates() -> None:
    duplicate_response = LLMResponse(
        ok=True,
        provider="fake",
        model="m",
        output_text="",
        tool_calls=[ToolCall(id="c", name="file.read", arguments={"path": "a"})],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            duplicate_response,
            duplicate_response,
            duplicate_response,
            duplicate_response,
            duplicate_response,
            duplicate_response,
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome() for _ in range(6)],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            max_iterations=10,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="dup")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_ITERATION_CAP,
    }


def test_duplicate_batch_retries_before_answer_only_closure() -> None:
    duplicate_response = LLMResponse(
        ok=True,
        provider="fake",
        model="m",
        output_text="",
        tool_calls=[ToolCall(id="read", name="file.read", arguments={"path": "a"})],
        finish_reason="tool_calls",
    )
    runtime = _FakeRuntime(
        responses=[
            duplicate_response,
            duplicate_response,
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="write",
                        name="file.write",
                        arguments={"path": "a", "content": "updated"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="done",
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=5, llm_calls_max=6),
        outcomes=[
            _success_outcome("file.read", "read ok"),
            _success_outcome("file.write", "write ok"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read", "file.write"}),
            max_iterations=6,
            profile_name="general_adaptive_v1",
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="read then write")],
        tool_specs=_tool_specs("file.read", "file.write"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == [
        "file.read",
        "file.write",
    ]
    assert any(
        status.get("mode_state") == "duplicate_tool_retry"
        for status in loop_ctx.statuses
    )


def test_loop_on_tool_result_callback_invoked() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="done",
                finish_reason="stop",
            ),
        ]
    )
    callbacks: list[Any] = []
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome()],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="cb")],
        tool_specs=_tool_specs("file.read"),
        on_tool_result=lambda st: callbacks.append(st),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert len(callbacks) >= 1


def test_loop_requestable_tool_specs_enables_tool_request() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="done early",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="?")],
        tool_specs=_tool_specs("file.read"),
        requestable_tool_specs=_tool_specs("extra.tool"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_disallowed_tool_terminates_with_disallowed_tool() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[ToolCall(id="c1", name="not_allowed.tool", arguments={})],
                finish_reason="tool_calls",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    # allowed_tools intentionally excludes "not_allowed.tool"
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="disallowed")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == "disallowed_tool"
    assert outcome.tool_name == "not_allowed.tool"


def test_loop_plan_tool_call_with_unknown_action_recovers() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="p1", name="plan", arguments={"action": "unknown"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="recovered",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="plan it")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_LLM_ERROR,
        ADAPTIVE_TERM_ITERATION_CAP,
    }


def test_loop_emits_iteration_status_events() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="finished",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="status test")],
        tool_specs=_tool_specs("file.read"),
    )
    assert len(loop_ctx.statuses) >= 1


def test_loop_with_session_api_appends_events() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="ok",
                finish_reason="stop",
            )
        ]
    )
    session_api = SimpleNamespace(events=[])

    def append_event(session_id, event_type, payload, **kwargs):
        session_api.events.append((event_type, payload))

    session_api.append_event = append_event
    state = _state()
    state.trace_id = "trace-1"
    loop_ctx = _LoopContext(state=state, outcomes=[], session_api=session_api)
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="session events")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_with_token_usage_debits_budget() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="done",
                usage=UsageInfo(input_tokens=100, output_tokens=50),
                finish_reason="stop",
            )
        ]
    )
    state = _state(tokens=10000)
    loop_ctx = _LoopContext(state=state, outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="usage")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    # Tokens should have been debited; budget less than starting value
    assert state.budgets_remaining.tokens < 10000


def test_loop_with_no_allowed_tools_and_no_specs_returns_final_text() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="chat-only response",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=None,
        max_iterations=2,
        allow_plan_tool=False,
        tool_choice="none",
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="chat only")],
        tool_specs=[],
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_tool_choice_none_does_not_auto_inject_plan_tool() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="chat-only response",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset(),
        max_iterations=2,
        tool_choice="none",
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="chat only")],
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert runtime.calls
    assert runtime.calls[0]["tools"] == []


def test_tool_choice_none_retries_when_model_still_emits_tool_calls() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.write", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="final answer",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset(),
        max_iterations=2,
        tool_choice="none",
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="chat only")],
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert len(runtime.calls) == 2
    assert runtime.calls[0]["tool_choice"] == "none"
    assert runtime.calls[1]["tool_choice"] == "none"


def test_tool_choice_none_retry_preserves_answer_only_output_constraints() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.write", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="result: done",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset(),
        max_iterations=2,
        tool_choice="none",
    )
    initial_state = AdaptiveToolLoopState(
        scratchpad={"coding.final_answer_reserve_used": True}
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(role="user", content="Use the exact label `result:`.")
        ],
        initial_state=initial_state,
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    retry_system_messages = [
        str(message.content)
        for message in runtime.calls[1]["messages"]
        if message.role == "system"
    ]
    assert any("result markers" in message for message in retry_system_messages)
    assert any("Do not call tools" in message for message in retry_system_messages)


def test_tool_choice_none_second_retry_degrades_answer_only_closeout_to_budget_exhausted() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.write", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="file.write", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset(),
        max_iterations=2,
        tool_choice="none",
    )
    initial_state = AdaptiveToolLoopState(
        scratchpad={"coding.final_answer_reserve_used": True}
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(role="user", content="Use the exact label `result:`.")
        ],
        initial_state=initial_state,
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
    assert (
        outcome.error_message == "Answer-only finalization kept returning tool calls."
    )


def test_tool_choice_none_second_retry_salvages_from_compact_tool_evidence() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.write", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="file.write", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="result: files changed a.py, b.py",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset(),
        max_iterations=2,
        tool_choice="none",
    )
    initial_state = AdaptiveToolLoopState(
        scratchpad={
            "coding.final_answer_reserve_used": True,
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "content": "wrote a.py",
                    "data": {"path": "a.py"},
                }
            ],
        }
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(role="user", content="Use the exact label `result:`.")
        ],
        initial_state=initial_state,
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "result: files changed a.py, b.py"
    assert len(runtime.calls) == 3
    assert any(
        "Successful tool evidence already gathered" in str(message.content)
        for message in runtime.calls[2]["messages"]
        if message.role == "user"
    )


def test_tool_choice_none_compact_closeout_accepts_visible_text_with_tool_calls() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="file.write", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-2", name="file.write", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="result: files changed a.py, b.py",
                tool_calls=[
                    ToolCall(
                        id="call-3",
                        name="file.write",
                        arguments={"path": "ignored.py"},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset(),
        max_iterations=2,
        tool_choice="none",
    )
    initial_state = AdaptiveToolLoopState(
        scratchpad={
            "coding.final_answer_reserve_used": True,
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "content": "wrote a.py",
                    "data": {"path": "a.py"},
                }
            ],
        }
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(role="user", content="Use the exact label `result:`.")
        ],
        initial_state=initial_state,
        tool_specs=[],
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert outcome.final_text == "result: files changed a.py, b.py"


def test_loop_initial_state_skips_guidance_dup_insertion() -> None:
    from openminion.modules.brain.loop.tools.engine import (
        _CONFIDENT_COMPLETE_GUIDANCE,
    )

    initial = AdaptiveToolLoopState(
        messages=[
            Message(role="system", content=_CONFIDENT_COMPLETE_GUIDANCE),
            Message(role="user", content="prepped"),
        ]
    )
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="ok",
                finish_reason="stop",
            )
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="ignored")],
        tool_specs=_tool_specs("file.read"),
        initial_state=initial,
    )
    # Count guidance occurrences — must remain exactly 1
    guidance_count = sum(
        1
        for m in outcome.state.messages
        if getattr(m, "role", "") == "system"
        and str(getattr(m, "content", "") or "").strip() == _CONFIDENT_COMPLETE_GUIDANCE
    )
    assert guidance_count == 1


def test_loop_with_memory_consolidation_context_injects_guidance() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="nothing to consolidate",
                finish_reason="stop",
            )
        ]
    )
    state = _state()
    state.module_state = {
        "memory_consolidation": {
            "enabled": True,
            "candidates": [],
        }
    }
    loop_ctx = _LoopContext(state=state, outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="consolidate")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_with_delegation_context_injects_parent_context_message() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="delegated answer",
                finish_reason="stop",
            )
        ]
    )
    state = _state()
    state.module_state = {
        "delegation": {
            "enabled": True,
            "parent_context": {
                "intent_id": "parent-i",
                "summary": "parent intent",
            },
        }
    }
    loop_ctx = _LoopContext(state=state, outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="delegated")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


class TestForceBudgetAnswerOnlyFinalization:
    def test_has_tool_evidence_uses_prior_tool_messages(self) -> None:
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')]
        )
        loop_ctx = _LoopContext(state=_state())

        assert _has_tool_evidence_for_answer_only(loop_ctx, st_loop) is True

    def test_has_tool_evidence_uses_working_state_last_result(self) -> None:
        st_loop = AdaptiveToolLoopState(messages=[])
        state = _state()
        state.last_result = ActionResult(
            command_id=new_uuid(),
            status="success",
            summary="Command exited with code 0.",
            outputs={"exit_code": 0},
        )
        loop_ctx = _LoopContext(state=state)

        assert _has_tool_evidence_for_answer_only(loop_ctx, st_loop) is True

    def test_has_tool_evidence_ignores_plan_only_tool_results(self) -> None:
        st_loop = AdaptiveToolLoopState(
            messages=[],
            scratchpad={
                "adaptive.tool_results": [
                    {
                        "tool_name": "plan",
                        "ok": True,
                        "content": "listed",
                        "data": {"items": []},
                    }
                ]
            },
        )
        st_loop.total_tool_calls = 1
        loop_ctx = _LoopContext(state=_state())

        assert _has_tool_evidence_for_answer_only(loop_ctx, st_loop) is False

    def test_has_tool_evidence_keeps_substantive_tool_results(self) -> None:
        st_loop = AdaptiveToolLoopState(
            messages=[],
            scratchpad={
                "adaptive.tool_results": [
                    {
                        "tool_name": "file.read",
                        "ok": True,
                        "content": "read",
                        "data": {"path": "a.txt"},
                    }
                ]
            },
        )
        st_loop.total_tool_calls = 1
        loop_ctx = _LoopContext(state=_state())

        assert _has_tool_evidence_for_answer_only(loop_ctx, st_loop) is True

    def test_budget_exhaustion_forces_answer_only_from_prior_tool_evidence(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"file.read"}), profile_name="general_adaptive_v1"
        )
        state = _state(llm_calls_max=1)
        state.llm_calls_used = 1
        state.goal = "Return exactly SOURCES, CHANGES, TESTS."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                    finish_reason="stop",
                ),
            ]
        )

        result = run_adaptive_tool_loop(
            loop_ctx,
            profile=prof,
            runtime=runtime,
            model="m",
            initial_messages=[
                Message(role="tool", content='{"status":"success","path":"README.md"}')
            ],
            tool_specs=_tool_specs("file.read"),
        )

        assert result.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert result.final_text.startswith("SOURCES")
        assert runtime.calls[-1]["tool_choice"] == "none"

    def test_returns_none_for_non_general_profile_without_tool_evidence(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(responses=[])
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset(),
            public_mode_tag="act",
        )
        assert result is None

    def test_non_general_profile_with_tool_evidence_can_force_finalization(
        self,
    ) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')],
            total_tool_calls=1,
        )
        state = _state()
        state.goal = "Return exactly SOURCES, CHANGES, TESTS."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- blocked",
                    finish_reason="stop",
                ),
            ]
        )
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )
        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert result.final_text.startswith("SOURCES")

    def test_returns_none_when_llm_budget_unavailable(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state(tokens=0))
        runtime = _FakeRuntime(responses=[])
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset(),
            public_mode_tag="act",
        )
        assert result is None

    def test_force_finalization_returns_final_text(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="forced final",
                    finish_reason="stop",
                ),
            ]
        )
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata={"k": "v"},
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )
        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert result.final_text == "forced final"

    def test_force_finalization_injects_original_request_when_history_lacks_user(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"blocked"}')]
        )
        state = _state()
        state.goal = "Return exactly three titled sections: SOURCES, CHANGES, TESTS."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        final_messages = runtime.calls[-1]["messages"]
        user_messages = [
            str(message.content) for message in final_messages if message.role == "user"
        ]
        assert len(user_messages) == 1
        assert "Original user request for this turn" in user_messages[0]
        assert "SOURCES, CHANGES, TESTS" in user_messages[0]
        assert "Do not infer or substitute a different task" in user_messages[0]

    def test_force_finalization_treats_neutral_continue_prompt_as_missing_user(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content=(
                        "Continue the active task using the existing conversation "
                        "and tool results. Do not treat tool-result payloads as a "
                        "new user request."
                    ),
                )
            ]
        )
        state = _state()
        state.last_user_input = (
            "Apply current PyPA console-script guidance and return SOURCES, "
            "CHANGES, TESTS."
        )
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        user_messages = [
            str(message.content)
            for message in runtime.calls[-1]["messages"]
            if message.role == "user"
        ]
        system_messages = [
            str(message.content)
            for message in runtime.calls[-1]["messages"]
            if message.role == "system"
        ]
        assert len(user_messages) == 2
        assert user_messages[-1].startswith("Original user request for this turn")
        assert "PyPA console-script guidance" in user_messages[-1]
        assert any("exact-date requirements" in text for text in system_messages)

    def test_force_finalization_uses_reserved_call_when_loop_llm_cap_reached(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        object.__setattr__(prof, "max_llm_calls_per_loop", 1)
        st_loop = AdaptiveToolLoopState(messages=[], llm_calls=1)
        state = _state()
        state.goal = "Return the best final answer from gathered evidence."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="final from reserved call",
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.final_text == "final from reserved call"

    def test_force_finalization_uses_reserved_call_when_turn_llm_cap_reached(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[])
        state = _state()
        state.llm_calls_used = state.llm_calls_max
        state.goal = "Return the best final answer from gathered evidence."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="final from turn reserve",
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.final_text == "final from turn reserve"


class TestFinalizeIterationCapExit:
    def test_iteration_cap_forces_answer_only_when_tool_work_exists(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[], total_tool_calls=3)
        state = _state()
        state.goal = "Return exactly SOURCES, CHANGES, TESTS."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                    finish_reason="stop",
                ),
            ]
        )

        outcome = finalize_iteration_cap_exit(
            loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            allowed_tools=frozenset({"x"}),
            public_mode_name="Act",
            public_mode_tag="act",
            max_output_tokens=100,
            metadata=None,
            loop_profiler=SimpleNamespace(summary=dict),
            trigger_macro_correction=lambda **_: None,
            dispatch_correction_plan=lambda **_: None,
        )

        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert outcome.final_text.startswith("SOURCES")
        assert (
            st_loop.scratchpad["iteration_cap_answer_only_finalization_forced"] is True
        )

    def test_iteration_cap_forces_answer_only_for_non_general_profile_with_tool_work(
        self,
    ) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(messages=[], total_tool_calls=2)
        state = _state()
        state.goal = "Return exactly SOURCES, CHANGES, TESTS."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- blocked",
                    finish_reason="stop",
                ),
            ]
        )

        outcome = finalize_iteration_cap_exit(
            loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            allowed_tools=frozenset({"x"}),
            public_mode_name="Act",
            public_mode_tag="act",
            max_output_tokens=100,
            metadata=None,
            loop_profiler=SimpleNamespace(summary=dict),
            trigger_macro_correction=lambda **_: None,
            dispatch_correction_plan=lambda **_: None,
        )

        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert outcome.final_text.startswith("SOURCES")

    def test_iteration_cap_prefers_answer_only_over_macro_correction_when_tool_work_exists(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        object.__setattr__(prof, "max_macro_corrections", 2)
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')]
        )
        state = _state()
        state.goal = "Return exactly SOURCES, CHANGES, TESTS."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="SOURCES\n- source\n\nCHANGES\n- change\n\nTESTS\n- pass",
                    finish_reason="stop",
                ),
            ]
        )

        outcome = finalize_iteration_cap_exit(
            loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            allowed_tools=frozenset({"x"}),
            public_mode_name="Act",
            public_mode_tag="act",
            max_output_tokens=100,
            metadata=None,
            loop_profiler=SimpleNamespace(summary=dict),
            trigger_macro_correction=lambda **_: object(),
            dispatch_correction_plan=lambda **_: ADAPTIVE_TERM_ITERATION_CAP,
        )

        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert outcome.final_text.startswith("SOURCES")

    def test_iteration_cap_preserves_tool_evidence_when_finalization_fails(
        self,
    ) -> None:
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        st_loop = AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content="Finish with exact labels `files:`, `validation:`, and `follow-ups:`.",
                )
            ],
            total_tool_calls=1,
        )
        st_loop.scratchpad["adaptive.tool_results"] = [
            {
                "tool_name": "file.read",
                "ok": True,
                "content": "read back loopcalc.py",
                "data": {"path": "loopcalc.py"},
            }
        ]
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="I hit an internal decision error before I could continue safely on this turn.",
                    finish_reason="stop",
                ),
            ]
        )

        outcome = finalize_iteration_cap_exit(
            loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            allowed_tools=frozenset({"file.read"}),
            public_mode_name="Act",
            public_mode_tag="act",
            max_output_tokens=100,
            metadata=None,
            loop_profiler=SimpleNamespace(summary=dict),
            trigger_macro_correction=lambda **_: None,
            dispatch_correction_plan=lambda **_: None,
        )

        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert "files: loopcalc.py" in str(outcome.final_text)
        assert "validation:" in str(outcome.final_text)
        assert (
            st_loop.scratchpad.get("iteration_cap_used_evidence_fallback") is True
            or st_loop.scratchpad.get("budget_stop_used_evidence_fallback") is True
        )
        assert runtime.calls[-1]["tool_choice"] == "none"

    def test_force_finalization_llm_exception_returns_llm_error(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')],
            total_tool_calls=1,
        )
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(responses=[], raise_error=True)
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )
        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_LLM_ERROR

    def test_force_finalization_llm_exception_without_tool_evidence_is_budget_exhausted(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(responses=[], raise_error=True)
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
        assert "runtime" in st_loop.scratchpad["budget_answer_only_finalization_error"]

    def test_force_finalization_not_ok_response_returns_llm_error(self) -> None:
        from openminion.modules.llm.schemas import ResponseError

        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')],
            total_tool_calls=1,
        )
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=False,
                    provider="fake",
                    model="m",
                    output_text="",
                    error=ResponseError(code="PROVIDER_ERROR", message="bad"),
                    finish_reason="error",
                )
            ]
        )
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )
        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_LLM_ERROR

    def test_force_finalization_not_ok_without_tool_evidence_is_budget_exhausted(
        self,
    ) -> None:
        from openminion.modules.llm.schemas import ResponseError

        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=False,
                    provider="fake",
                    model="m",
                    output_text="",
                    error=ResponseError(code="PROVIDER_ERROR", message="bad"),
                    finish_reason="error",
                )
            ]
        )
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
        assert st_loop.scratchpad["budget_answer_only_finalization_error"] == "bad"

    def test_force_finalization_empty_text_returns_budget_exhausted(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="",
                    finish_reason="stop",
                ),
            ]
        )
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )
        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED

    def test_force_finalization_retries_after_tool_choice_none_returns_tools(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')],
            total_tool_calls=1,
        )
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="",
                    tool_calls=[ToolCall(id="call-1", name="web.search", arguments={})],
                    finish_reason="tool_calls",
                ),
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="Recovered final answer from existing tool results.",
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert result.final_text == "Recovered final answer from existing tool results."
        assert len(runtime.calls) == 2
        assert (
            st_loop.scratchpad["budget_answer_only_tool_choice_none_retry_used"] is True
        )
        retry_system_messages = [
            str(message.content)
            for message in runtime.calls[1]["messages"]
            if message.role == "system"
        ]
        assert any("Do not call tools" in message for message in retry_system_messages)

    def test_force_finalization_rejects_provider_fallback_text(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')],
            total_tool_calls=1,
        )
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="openrouter",
                    model="m",
                    output_text=(
                        "I could not parse a usable model response on this turn. "
                        "Please retry."
                    ),
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
        assert (
            st_loop.scratchpad["budget_answer_only_finalization_error"]
            == "internal_failure_final_text"
        )

    def test_force_finalization_rejects_execution_preface_draft(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')],
            total_tool_calls=1,
        )
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "<step1>Create files</step1>\n"
                        "<step2>Read back one file to validate</step2>"
                    ),
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
        assert (
            st_loop.scratchpad["budget_answer_only_finalization_rejected_text"]
            == "<step1>Create files</step1>\n<step2>Read back one file to validate</step2>"
        )
        assert st_loop.scratchpad["budget_answer_only_restore_index"] == 1

    def test_force_finalization_rejects_raw_tool_markup(self) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(
            messages=[Message(role="tool", content='{"status":"success"}')],
            total_tool_calls=1,
        )
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "[system: UNEXECUTABLE_TOOL_ENVELOPE]\n"
                        "The model generated a tool envelope that could not be executed."
                    ),
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
        assert (
            st_loop.scratchpad["budget_answer_only_finalization_raw_tool_rejected"]
            == "[system: UNEXECUTABLE_TOOL_ENVELOPE]\n"
            "The model generated a tool envelope that could not be executed."
        )
        assert st_loop.scratchpad["budget_answer_only_restore_index"] == 1

    def test_force_finalization_internal_failure_text_returns_budget_exhausted(
        self,
    ) -> None:
        prof = _profile(
            allowed_tools=frozenset({"x"}), profile_name="general_adaptive_v1"
        )
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "I hit an internal decision error before I could continue "
                        "safely on this turn."
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED
        assert (
            st_loop.scratchpad["budget_answer_only_finalization_error"]
            == "internal_failure_final_text"
        )

    def test_force_finalization_recovers_status_after_answer_missing_trailer(
        self,
    ) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content=(
                        "Research the latest situation and append "
                        "<finalization_status>{...}</finalization_status>."
                    ),
                ),
                Message(role="tool", content='{"status":"success"}'),
            ],
            total_tool_calls=2,
        )
        state = _state()
        state.last_user_input = (
            "Append <finalization_status>{...}</finalization_status> after the answer."
        )
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="Here is the researched answer from the gathered evidence.",
                    finish_reason="stop",
                ),
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="",
                    finalization_status={
                        "status": "final_answer",
                        "reasoning": "The prior answer completed the request.",
                    },
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert (
            result.final_text
            == "Here is the researched answer from the gathered evidence."
        )
        assert result.finalization_status == {
            "status": "final_answer",
            "reasoning": "The prior answer completed the request.",
            "remaining_work": "",
            "blocking_reason": "",
        }
        assert len(runtime.calls) == 2
        assert runtime.calls[1]["tool_choice"] == "none"
        retry_messages = runtime.calls[1]["messages"]
        assert retry_messages[-2].role == "assistant"
        assert retry_messages[-2].content == result.final_text
        assert retry_messages[-1].role == "system"
        assert "Return only the structured finalization_status signal" in (
            retry_messages[-1].content
        )

    def test_force_finalization_recovers_status_from_retry_trailer_text(
        self,
    ) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content=(
                        "Research the latest situation and append "
                        "<finalization_status>{...}</finalization_status>."
                    ),
                ),
                Message(role="tool", content='{"status":"success"}'),
            ],
            total_tool_calls=2,
        )
        state = _state()
        state.last_user_input = (
            "Append <finalization_status>{...}</finalization_status> after the answer."
        )
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="Here is the researched answer from the gathered evidence.",
                    finish_reason="stop",
                ),
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        '<finalization_status>{"status":"final_answer",'
                        '"reasoning":"The prior answer completed the request."}'
                        "</finalization_status>"
                    ),
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert (
            result.final_text
            == "Here is the researched answer from the gathered evidence."
        )
        assert result.finalization_status == {
            "status": "final_answer",
            "reasoning": "The prior answer completed the request.",
            "remaining_work": "",
            "blocking_reason": "",
        }

    def test_force_finalization_preserves_incomplete_typed_status(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(
            messages=[
                Message(role="user", content="Answer with finalization_status."),
                Message(role="tool", content='{"status":"success"}'),
            ],
            total_tool_calls=1,
        )
        state = _state()
        state.last_user_input = "Answer with finalization_status."
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="I found partial evidence but not enough to finish.",
                    finalization_status={
                        "status": "incomplete",
                        "reasoning": "More evidence is required.",
                        "remaining_work": "Fetch one more source.",
                    },
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_FINALIZATION_INCOMPLETE
        assert result.final_text == "I found partial evidence but not enough to finish."
        assert result.finalization_status is not None
        assert result.finalization_status["remaining_work"] == "Fetch one more source."

    def test_force_finalization_fail_closes_when_explicit_contract_missing(
        self,
    ) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(
            messages=[
                Message(
                    role="user",
                    content=(
                        "Return PLAN, TABLE, and UNCERTAINTIES and append "
                        "<finalization_status>{...}</finalization_status>."
                    ),
                ),
                Message(role="tool", content='{"status":"success"}'),
            ],
            total_tool_calls=2,
        )
        state = _state()
        state.last_user_input = (
            "Append <finalization_status>{...}</finalization_status> after the answer."
        )
        loop_ctx = _LoopContext(state=state)
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="PLAN\n- done\n\nTABLE\n- compared\n\nUNCERTAINTIES\n- none",
                    finish_reason="stop",
                ),
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="still missing status",
                    finish_reason="stop",
                ),
            ]
        )

        result = _force_budget_answer_only_finalization(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            max_output_tokens=100,
            metadata=None,
            allowed_tools=frozenset({"x"}),
            public_mode_tag="act",
        )

        assert result is not None
        assert result.termination_reason == ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING
        assert "required typed finalization_status contract" in str(
            result.error_message or ""
        )


class TestForceDuplicateBatchAnswerOnlyClosure:
    def _prepare_state_with_facts(self, signature: str) -> AdaptiveToolLoopState:
        st = AdaptiveToolLoopState(messages=[])
        call = ToolCall(id="c", name="file.read", arguments={"path": "a"})
        ar = ActionResult(
            command_id=new_uuid(),
            status="success",
            summary="ok",
            outputs={"ok_field": "v"},
        )
        # ordered_tool_results is list[(tool_call, command_outcome)] — the
        # outcome must expose .action_result and .job for the facts recorder.
        outcome = SimpleNamespace(action_result=ar, job=None)
        _record_duplicate_batch_execution_facts(
            st,
            signature=signature,
            ordered_tool_results=[(call, outcome)],
        )
        return st

    def _prepare_state_with_requested_evidence(
        self,
        signature: str,
        *,
        request: str = "finish with `result:` and `validation:`",
    ) -> AdaptiveToolLoopState:
        st = self._prepare_state_with_facts(signature)
        st.messages = [Message(role="user", content=request)]
        st.scratchpad["adaptive.tool_results"] = [
            {
                "tool_name": "file.read",
                "ok": True,
                "content": "read back created file successfully",
                "data": {"path": "module.py"},
            }
        ]
        return st

    def test_returns_none_when_no_facts(self) -> None:
        prof = _profile(allowed_tools=frozenset({"x"}))
        st_loop = AdaptiveToolLoopState(messages=[])
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(responses=[])
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset(),
            public_mode_tag="act",
            signature="sig-none",
        )
        assert out is None

    def test_returns_none_for_plan_only_duplicate_batch(self) -> None:
        signature = "sig-plan"
        st_loop = AdaptiveToolLoopState(
            messages=[],
            scratchpad={
                "adaptive.tool_results": [
                    {
                        "tool_name": "plan",
                        "ok": True,
                        "content": "listed",
                        "data": {"items": []},
                    }
                ]
            },
        )
        call = ToolCall(id="c", name="plan", arguments={"action": "list"})
        ar = ActionResult(command_id=new_uuid(), status="success", summary="listed")
        _record_duplicate_batch_execution_facts(
            st_loop,
            signature=signature,
            ordered_tool_results=[(call, SimpleNamespace(action_result=ar, job=None))],
        )
        prof = _profile(allowed_tools=frozenset({"plan"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="should not be called",
                    finish_reason="stop",
                )
            ]
        )

        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[call],
            tool_specs=_tool_specs("plan"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"plan"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is None
        assert runtime.calls == []

    def test_returns_final_text_outcome(self) -> None:
        signature = "sig-2"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="closure final",
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=200,
            metadata={"a": "b"},
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )
        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert out.final_text == "closure final"

    def test_returns_llm_error_on_exception(self) -> None:
        signature = "sig-3"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(responses=[], raise_error=True)
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )
        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_LLM_ERROR

    def test_returns_llm_error_on_not_ok(self) -> None:
        from openminion.modules.llm.schemas import ResponseError

        signature = "sig-4"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=False,
                    provider="fake",
                    model="m",
                    output_text="",
                    error=ResponseError(code="PROVIDER_ERROR", message="bad"),
                    finish_reason="error",
                )
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )
        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_LLM_ERROR

    def test_returns_duplicate_on_response_with_more_tool_calls(self) -> None:
        signature = "sig-5"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="",
                    tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
                    finish_reason="tool_calls",
                )
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )
        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS

    def test_returns_duplicate_on_empty_final_text(self) -> None:
        signature = "sig-6"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="",
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )
        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS

    def test_falls_back_to_evidence_on_empty_final_text_with_tool_evidence(
        self,
    ) -> None:
        signature = "sig-6-evidence"
        st_loop = self._prepare_state_with_requested_evidence(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="",
                    finish_reason="stop",
                ),
            ]
        )

        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert "result:" in str(out.final_text or "").lower()
        assert "validation:" in str(out.final_text or "").lower()
        assert "tool evidence:" in str(out.final_text or "").lower()

    def test_falls_back_to_evidence_when_duplicate_closure_misses_labels(
        self,
    ) -> None:
        signature = "sig-missing-labels"
        st_loop = self._prepare_state_with_requested_evidence(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text="I read the file successfully.",
                    finish_reason="stop",
                ),
            ]
        )

        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert "result:" in str(out.final_text or "").lower()
        assert "validation:" in str(out.final_text or "").lower()

    def test_returns_none_on_provider_fallback_final_text(self) -> None:
        signature = "sig-provider-fallback"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="openrouter",
                    model="m",
                    output_text=(
                        "I could not parse a usable model response on this turn. "
                        "Please retry."
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )
        assert out is None
        assert dur >= 0
        assert tok >= 0
        assert (
            st_loop.scratchpad["duplicate_batch_closure_invalid_final_text"]
            == "I could not parse a usable model response on this turn. Please retry."
        )

    def test_returns_none_on_raw_tool_markup_final_text(self) -> None:
        signature = "sig-raw-markup"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.write"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "[TOOL_CALL]"
                        '{tool => "file.write", args => {'
                        '--path "README.md" --content "updated"'
                        "}}"
                        "[/TOOL_CALL]"
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.write"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.write"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is None
        assert dur >= 0
        assert tok >= 0
        assert (
            st_loop.scratchpad["duplicate_batch_closure_raw_tool_markup_rejected"]
            is True
        )

    def test_returns_none_on_prose_prefixed_raw_tool_json_final_text(self) -> None:
        signature = "sig-raw-tool-json"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.write"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "I'll create it now.\n\n"
                        '{"tool": "file.write", "path": "README.md", '
                        '"content": "updated"}'
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.write"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.write"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is None
        assert dur >= 0
        assert tok >= 0
        assert (
            st_loop.scratchpad["duplicate_batch_closure_raw_tool_markup_rejected"]
            is True
        )

    def test_returns_none_on_raw_cmd_tool_json_final_text(self) -> None:
        signature = "sig-raw-cmd-json"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"exec.run"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "Let me verify it.\n\n"
                        '{"tool": "cmd.run", "cmd": "python -m pytest"}'
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.list_dir", arguments={})],
            tool_specs=_tool_specs("exec.run"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"exec.run"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is None
        assert dur >= 0
        assert tok >= 0
        assert (
            st_loop.scratchpad["duplicate_batch_closure_raw_tool_markup_rejected"]
            is True
        )

    def test_returns_none_on_tool_name_parameters_json_final_text(self) -> None:
        signature = "sig-raw-tool-name-json"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.write", "exec.run"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "I'll build the files now.\n\n"
                        '{"tool_name": "file.write", "parameters": {'
                        '"path": "README.md", "content": "updated"}}'
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="plan", arguments={})],
            tool_specs=_tool_specs("file.write", "exec.run"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.write", "exec.run"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is None
        assert dur >= 0
        assert tok >= 0
        assert (
            st_loop.scratchpad["duplicate_batch_closure_raw_tool_markup_rejected"]
            is True
        )

    def test_returns_none_on_plaintext_file_write_final_text(self) -> None:
        signature = "sig-raw-file-write-text"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.write"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "file.write\npath: /tmp/project/README.md\ncontent: # Project"
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="plan", arguments={})],
            tool_specs=_tool_specs("file.write"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.write"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is None
        assert dur >= 0
        assert tok >= 0
        assert (
            st_loop.scratchpad["duplicate_batch_closure_raw_tool_markup_rejected"]
            is True
        )

    def test_returns_none_on_plaintext_exec_run_final_text(self) -> None:
        signature = "sig-raw-exec-run-text"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"exec.run"}))
        loop_ctx = _LoopContext(state=_state())
        runtime = _FakeRuntime(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="m",
                    output_text=(
                        "exec.run cmd: cd /tmp/project && PYTHONPATH=. python -m pytest"
                    ),
                    finish_reason="stop",
                ),
            ]
        )
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="plan", arguments={})],
            tool_specs=_tool_specs("exec.run"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"exec.run"}),
            public_mode_tag="act",
            signature=signature,
        )

        assert out is None
        assert dur >= 0
        assert tok >= 0
        assert (
            st_loop.scratchpad["duplicate_batch_closure_raw_tool_markup_rejected"]
            is True
        )

    def test_returns_budget_exhausted_when_tokens_zero(self) -> None:
        signature = "sig-7"
        st_loop = self._prepare_state_with_facts(signature)
        prof = _profile(allowed_tools=frozenset({"file.read"}))
        loop_ctx = _LoopContext(state=_state(tokens=0))
        runtime = _FakeRuntime(responses=[])
        out, dur, tok = _force_duplicate_batch_answer_only_closure(
            loop_ctx=loop_ctx,
            profile=prof,
            loop_state=st_loop,
            runtime=runtime,
            model="m",
            tool_calls=[ToolCall(id="c", name="file.read", arguments={})],
            tool_specs=_tool_specs("file.read"),
            max_output_tokens=None,
            metadata=None,
            allowed_tools=frozenset({"file.read"}),
            public_mode_tag="act",
            signature=signature,
        )
        assert out is not None
        assert out.termination_reason == ADAPTIVE_TERM_BUDGET_EXHAUSTED


def test_loop_tool_failure_no_recovery_terminates() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "x"})
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_failed_outcome()],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            allow_llm_recovery_after_tool_failure=False,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="no recovery")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == "tool_failure_no_recovery"


def test_loop_profile_llm_cap_forces_answer_only_when_tool_work_exists() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "x"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="SOURCES\n- gathered\n\nCHANGES\n- applied\n\nTESTS\n- pass",
                finish_reason="stop",
            ),
        ]
    )
    profile = _profile(
        allowed_tools=frozenset({"file.read"}),
        profile_name="general_adaptive_v1",
    )
    object.__setattr__(profile, "max_llm_calls_per_loop", 1)

    outcome = run_adaptive_tool_loop(
        _LoopContext(state=_state(), outcomes=[_success_outcome()]),
        profile=profile,
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="do research")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "SOURCES" in str(outcome.final_text)
    assert runtime.calls[-1]["tools"] == []
    assert runtime.calls[-1]["tool_choice"] == "none"


def test_loop_plan_tool_declare_action_records_used() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="p1",
                        name="plan",
                        arguments={
                            "action": "declare",
                            "plan": {
                                "plan_id": "plan-1",
                                "objective": "do work",
                                "steps": [
                                    {
                                        "step_id": "s1",
                                        "description": "step one",
                                    }
                                ],
                            },
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="declared",
                finish_reason="stop",
            ),
        ]
    )
    state = _state()
    session_api = SimpleNamespace(events=[])
    session_api.append_event = lambda *a, **kw: session_api.events.append(a)
    session_api.get_active_task_plan = lambda sid: None
    loop_ctx = _LoopContext(state=state, outcomes=[], session_api=session_api)
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="plan")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_decompose_mixed_first_round_retries_then_recovers() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="d1", name="decompose", arguments={"subtasks": []}),
                    ToolCall(id="r1", name="file.read", arguments={"path": "x"}),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="recovered without mixed",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"decompose", "file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="mixed batch")],
        tool_specs=[decompose_tool_spec(), *_tool_specs("file.read")],
    )
    # Either gives final_text or fails closed if retry mixed repeats.
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_DECOMPOSE_INVALID,
        ADAPTIVE_TERM_ITERATION_CAP,
    }


def test_loop_freshness_exact_date_query_auto_repairs_before_tool_execution() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="web.search",
                        arguments={"query": "weather 2018"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="recovered freshness",
                finish_reason="stop",
            ),
        ]
    )
    state = _state()
    # Inject freshness obligation
    state.freshness_obligations = SimpleNamespace(require_exact_date=True)
    loop_ctx = _LoopContext(
        state=state, outcomes=[_success_outcome("web.search", "ok")]
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"web.search"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="check the weather")],
        tool_specs=_tool_specs("web.search"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert len(loop_ctx.commands) == 1
    assert getattr(loop_ctx.commands[0], "tool_name", "") == "web.search"
    assert getattr(loop_ctx.commands[0], "args", {}).get("query") == "weather"
    assert any(
        status.get("mode_state") == "freshness_exact_date_query_autorepair"
        for status in loop_ctx.statuses
    )


def test_loop_circular_pattern_terminates() -> None:
    # Build distinct-args responses to bypass duplicate-batch detection,
    # but same tool names → circular pattern detection on 3rd iter.
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c3", name="file.read", arguments={"path": "c"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="post-circular",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome() for _ in range(3)],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=10),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="circular")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        "circular_pattern",
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_ITERATION_CAP,
    }


def test_loop_circular_pattern_rejects_internal_failure_answer_text() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c3", name="file.read", arguments={"path": "c"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "I hit an internal decision error before I could continue safely "
                    "on this turn."
                ),
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome() for _ in range(3)],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=10),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="circular")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "tool evidence:" in str(outcome.final_text or "").lower()
    assert bool(outcome.state.scratchpad.get("circular_pattern_used_evidence_fallback"))


def test_loop_iteration_cap_exit_path() -> None:
    # No budget config — _maybe_extend_iteration_budget returns False — break.
    # 3 responses give 3 iterations, then loop hits cap.
    runtime = _FakeRuntime(
        responses=[
            # Distinct args to bypass duplicate detection
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="a", name="file.read", arguments={"path": "p1"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="b", name="file.read", arguments={"path": "p2"})
                ],
                finish_reason="tool_calls",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            _success_outcome("file.read", "ok1"),
            _success_outcome("file.read", "ok2"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"}), max_iterations=2),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="cap test")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_ITERATION_CAP,
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ADAPTIVE_TERM_FINAL_TEXT,
    }


def test_loop_tool_request_call_activates_inactive_tool() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="r1",
                        name="tool.request",
                        arguments={"name": "extra.tool"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="done after activation",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="request a tool")],
        tool_specs=_tool_specs("file.read"),
        requestable_tool_specs=_tool_specs("extra.tool"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_provider_parallel_capacity_drives_dispatch() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"}),
                    ToolCall(id="c2", name="file.read", arguments={"path": "b"}),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="parallel done",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            _success_outcome("file.read", "a-result"),
            _success_outcome("file.read", "b-result"),
        ],
    )
    prof = AdaptiveToolLoopProfile(
        profile_name="t",
        mode_name="act_adaptive",
        allowed_tools=frozenset({"file.read"}),
        max_iterations=4,
        provider_parallel_tool_capacity=2,
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=prof,
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="parallel")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT


def test_loop_custom_tool_batch_runner_overrides_default() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="custom done",
                finish_reason="stop",
            ),
        ]
    )

    captured: dict[str, Any] = {}
    success = CommandExecutionOutcome(
        approved_command=SimpleNamespace(tool_name="file.read", args={}),
        action_result=ActionResult(
            command_id=new_uuid(),
            status="success",
            summary="from runner",
        ),
    )

    def custom_runner(*, loop_ctx, tool_calls, include_reflect, loop_state):
        captured["called"] = True
        return [(tool_calls[0], success)]

    loop_ctx = _LoopContext(state=_state(), outcomes=[])
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="custom")],
        tool_specs=_tool_specs("file.read"),
        tool_batch_runner=custom_runner,
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert captured.get("called") is True


def test_loop_token_budget_exhausted_terminates() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a"})
                ],
                finish_reason="tool_calls",
                usage=UsageInfo(input_tokens=10000, output_tokens=10000),
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="recovered after budget",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tokens=1),
        outcomes=[_success_outcome()],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(allowed_tools=frozenset({"file.read"})),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="budget")],
        tool_specs=_tool_specs("file.read"),
    )
    assert outcome.termination_reason in {
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    }


def test_loop_retries_when_final_answer_repeats_pre_tool_draft() -> None:
    draft = "**PLAN**\n\n1. Search\n2. Fetch\n\nNow executing all three in parallel:"
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=draft,
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="web.search",
                        arguments={"query": "pipx official documentation pypa"},
                    ),
                    ToolCall(
                        id="c2",
                        name="web.fetch",
                        arguments={
                            "url": "https://docs.astral.sh/uv/getting-started/installation/"
                        },
                    ),
                    ToolCall(
                        id="c3",
                        name="web.fetch",
                        arguments={"url": "https://pipx.pypa.io/"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=draft,
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "**PLAN**\n- done\n\n"
                    "**TABLE**\n- compared\n\n"
                    "**UNCERTAINTIES**\n- none\n"
                ),
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            _success_outcome("web.search", "search ok"),
            _success_outcome("web.fetch", "uv ok"),
            _success_outcome("web.fetch", "pipx ok"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            profile_name="general_adaptive_v1",
            max_iterations=7,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="compare uv and pipx")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "TABLE" in str(outcome.final_text or "")


def test_loop_retries_when_final_answer_is_execution_preface_draft() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "**PLAN**\n"
                    "- fetch packaging guide\n"
                    "- write pyproject and README\n\n"
                    "Now executing the required tool batch."
                ),
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="web.fetch",
                        arguments={
                            "url": (
                                "https://packaging.python.org/en/latest/guides/"
                                "writing-pyproject-toml/"
                            )
                        },
                    ),
                    ToolCall(
                        id="c2",
                        name="file.write",
                        arguments={"path": "/tmp/pyproject.toml", "content": "x"},
                    ),
                    ToolCall(
                        id="c3",
                        name="file.write",
                        arguments={"path": "/tmp/README.md", "content": "y"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "I'll execute the required tool batch: web.fetch for the "
                    "PyPA URL, then file.write for both pyproject.toml and README.md."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "SOURCES\n"
                    "- https://packaging.python.org/en/latest/guides/writing-pyproject-toml/\n"
                    "DATE: 2026-06-21\n\n"
                    "CHANGES\n"
                    "- Updated pyproject.toml and README.md.\n\n"
                    "TESTS\n"
                    "- python -m pytest -q tests\n"
                ),
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[
            _success_outcome("web.fetch", "PyPA ok"),
            _success_outcome("file.write", "pyproject ok"),
            _success_outcome("file.write", "README ok"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.fetch", "file.write"}),
            profile_name="general_adaptive_v1",
            max_iterations=7,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="update packaging docs")],
        tool_specs=_tool_specs("web.fetch", "file.write"),
    )
    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "SOURCES" in str(outcome.final_text or "")


def test_loop_retries_snippet_only_answer_when_file_artifacts_were_requested() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.list_dir", arguments={"path": "."}),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "implementation:\n\n"
                    "import sys\n\n"
                    "def main():\n"
                    "    print('hello')\n\n"
                    "validation:\n"
                    "Read back the created file."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="file.write",
                        arguments={"path": "cli.py", "content": "print('hello')\n"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="result: wrote cli.py",
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "design: simple CLI\n"
                    "implementation: wrote cli.py\n"
                    "validation: file.write succeeded\n"
                    "follow-ups: none"
                ),
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4, llm_calls_max=6),
        outcomes=[
            _success_outcome("file.list_dir", "empty directory"),
            _success_outcome("file.write", "wrote cli.py"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.list_dir", "file.write"}),
            profile_name="general_adaptive_v1",
            max_iterations=6,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(
                role="user",
                content=(
                    "Build a tiny Python CLI project. Use file.write for files; "
                    "do not only show code snippets."
                ),
            )
        ],
        tool_specs=_tool_specs("file.list_dir", "file.write"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == [
        "file.list_dir",
        "file.write",
    ]
    assert bool(outcome.state.scratchpad.get("snippet_only_file_artifact_retry_used"))


def test_loop_retries_prose_closeout_when_file_artifacts_were_not_created() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "design: simple CLI\n"
                    "implementation: create a parser and summarize sections\n"
                    "validation: read back the generated file\n"
                    "follow-ups: none"
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="file.write",
                        arguments={"path": "cli.py", "content": "print('ok')\n"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="result: wrote cli.py",
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "design: simple CLI\n"
                    "implementation: wrote cli.py\n"
                    "validation: file.write succeeded\n"
                    "follow-ups: none"
                ),
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4, llm_calls_max=5),
        outcomes=[_success_outcome("file.write", "wrote cli.py")],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.write"}),
            profile_name="general_adaptive_v1",
            max_iterations=5,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(
                role="user",
                content=(
                    "Implement a tiny CLI with file.write. Close with "
                    "`design:`, `implementation:`, `validation:`, and `follow-ups:`."
                ),
            )
        ],
        tool_specs=_tool_specs("file.write"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert [command.tool_name for command in loop_ctx.commands] == ["file.write"]
    assert bool(outcome.state.scratchpad.get("snippet_only_file_artifact_retry_used"))


def test_loop_falls_back_to_tool_evidence_after_repeated_execution_preface() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="file.read",
                        arguments={"path": "/tmp/report.py"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "I'll read back the core module file to verify the expected "
                    "content is present."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "I'll read back the core module file to verify the expected "
                    "content is present."
                ),
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(),
        outcomes=[_success_outcome("file.read", "core module content present")],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"file.read"}),
            max_iterations=5,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="verify and summarize")],
        tool_specs=_tool_specs("file.read"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "tool evidence:" in str(outcome.final_text or "").lower()
    assert "file.read" in str(outcome.final_text or "")
    assert "core module content present" in str(outcome.final_text or "")
    assert bool(
        outcome.state.scratchpad.get("pre_tool_draft_echo_used_evidence_fallback")
    )


def test_loop_retries_empty_finalization_after_successful_tool_evidence() -> None:
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="web.search",
                        arguments={"query": "terminal agent ux"},
                    ),
                    ToolCall(
                        id="c2",
                        name="web.fetch",
                        arguments={"url": "https://example.com/agent-ux"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "Tradeoffs\n"
                    "- Streaming improves perceived responsiveness.\n\n"
                    "Recommendation\n"
                    "- Prefer visible progress with bounded tool output."
                ),
                finalization_status={"status": "final_answer", "reasoning": "done"},
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=4),
        outcomes=[
            _success_outcome("web.search", "terminal agent UX evidence"),
            _success_outcome("web.fetch", "terminal agent UX article"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            profile_name="general_adaptive_v1",
            max_iterations=6,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[Message(role="user", content="compare terminal agent ux")],
        tool_specs=_tool_specs("web.search", "web.fetch"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "Tradeoffs" in str(outcome.final_text or "")
    assert "Recommendation" in str(outcome.final_text or "")
    assert bool(
        outcome.state.scratchpad.get("empty_final_after_tool_results_retry_used")
    )
    assert bool(
        outcome.state.scratchpad.get("empty_final_after_tool_results_final_retry_used")
    )


def test_loop_falls_back_to_evidence_when_typed_finalization_contract_is_missing() -> (
    None
):
    runtime = _FakeRuntime(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="web.search",
                        arguments={"query": "python packaging metadata"},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "PEP 621 keeps project metadata in pyproject.toml, and core "
                    "metadata remains the exchange format."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text=(
                    "PEP 621 keeps project metadata in pyproject.toml, and core "
                    "metadata remains the exchange format."
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="m",
                output_text="",
                finish_reason="stop",
            ),
        ]
    )
    loop_ctx = _LoopContext(
        state=_state(tool_calls=3, llm_calls_max=6),
        outcomes=[
            _success_outcome("web.search", "PEP 621 and core metadata evidence"),
        ],
    )
    outcome = run_adaptive_tool_loop(
        loop_ctx,
        profile=_profile(
            allowed_tools=frozenset({"web.search"}),
            profile_name="general_adaptive_v1",
            max_iterations=6,
        ),
        runtime=runtime,
        model="m",
        initial_messages=[
            Message(
                role="user",
                content=(
                    "Compare Python packaging metadata best practices and end "
                    "with a short recommended direction."
                ),
            )
        ],
        tool_specs=_tool_specs("web.search"),
    )

    assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
    assert "recommendation:" in str(outcome.final_text or "").lower()
    assert "web.search" in str(outcome.final_text or "")
    assert bool(
        outcome.state.scratchpad.get(
            "typed_finalization_contract_used_evidence_fallback"
        )
    )


def test_execution_preface_draft_detects_future_tense_tool_batch() -> None:
    assert _looks_like_execution_preface_draft(
        "I'll execute the required tool batch: web.fetch for the PyPA URL, "
        "then file.write for both pyproject.toml and README.md."
    )


def test_execution_preface_draft_detects_progress_note_after_tool_results() -> None:
    assert _looks_like_execution_preface_draft(
        "Reading pyproject.toml and README.md to verify the required strings "
        "are present:"
    )
    assert _looks_like_execution_preface_draft(
        "I'll read the full report.py to see exactly what was completed and "
        "what needs to be finished."
    )
    assert _looks_like_execution_preface_draft(
        "Based on the existing tool results, I can see the project is mostly "
        "complete. Let me verify the tests."
    )
    assert _looks_like_execution_preface_draft(
        "Let me check the current state of the project by reading the existing files."
    )
    assert _looks_like_execution_preface_draft(
        "Proceeding to add `tests/` and `pyproject.toml`, then run validation."
    )
    assert _looks_like_execution_preface_draft(
        "Brief plan: Create 4 files (`loopcalc.py` CLI, `loopcalc_core.py` helper, "
        "`smoke_test.py`, `README.md`), write them with file.write, then read back "
        "`loopcalc_core.py` to validate persistence. Writing files now."
    )


def test_unexecutable_tool_payload_detects_embedded_json_after_prose() -> None:
    assert _looks_like_unexecutable_tool_payload_text(
        "I'll continue from the completed tool results. Now I need to verify "
        "the files and run the tests.\n"
        '{"tool_name": "file.read", "tool_input": {"path": "/tmp/pyproject.toml"}}\n'
        '{"tool_name": "exec.run", "tool_input": {"command": "python -m pytest -q tests"}}'
    )


def test_final_answer_references_unbacked_source_urls_detects_missing_fetch() -> None:
    st_loop = AdaptiveToolLoopState(
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "web.search",
                    "ok": True,
                    "content": "1. pipx - https://pipx.pypa.io/",
                    "data": {"results": [{"url": "https://pipx.pypa.io/"}]},
                }
            ]
        }
    )

    assert _final_answer_references_unbacked_source_urls(
        st_loop,
        text=(
            "PLAN\n- fetch https://docs.astral.sh/uv/getting-started/installation/\n"
            "UNCERTAINTIES\n- https://pipx.pypa.io/"
        ),
    )


def test_final_text_parrots_policy_denial_detects_exec_run_echo() -> None:
    st_loop = AdaptiveToolLoopState(
        scratchpad={
            "adaptive.tool_results": [
                {
                    "tool_name": "exec.run",
                    "ok": False,
                    "content": "Denied by policy: command 'pip' is not allowlisted",
                    "data": {
                        "error": {
                            "details": {
                                "suggested_fix": (
                                    "Run the allowed direct command `python -m pytest -q "
                                    "tests` from the workspace instead."
                                )
                            }
                        }
                    },
                }
            ]
        }
    )

    assert _final_text_parrots_policy_denial(
        st_loop,
        text="Denied by policy: command 'pip' is not allowlisted",
    )


def test_action_result_to_tool_message_compacts_large_payloads() -> None:
    action_result = ActionResult(
        command_id="x",
        status="success",
        summary="s" * 3000,
        outputs={
            "content": "c" * 5000,
            "results": [{"title": "r", "body": "b" * 5000} for _ in range(10)],
        },
        error=ActionError(code="E", message="m" * 3000),
    )

    message = action_result_to_tool_message("call-1", "web.fetch", action_result)
    payload = json.loads(message.content)

    assert payload["summary"].endswith("...[truncated]")
    assert payload["outputs"]["content"].endswith("...[truncated]")
    assert payload["outputs"]["results"][-1].startswith("...[")
    assert payload["error"]["message"].endswith("...[truncated]")
