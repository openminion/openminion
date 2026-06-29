from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_JOB_PENDING,
    BRAIN_STATE_WAITING_USER,
    MEMORY_CONSOLIDATION_MODULE_STATE_KEY,
    WATCH_MODULE_STATE_KEY,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop import adaptive
from openminion.modules.brain.loop.adaptive import context as adaptive_context
from openminion.modules.brain.loop.adaptive import modes as adaptive_modes
from openminion.modules.brain.loop.adaptive import (
    ACT_ADAPTIVE_ALLOWED_TOOLS,
    ActLoopMode,
    _AdaptiveLoopContextAdapter,
    _active_plan_id,
    _active_step_ids,
    _adaptive_loop_metadata,
    _append_partial_success,
    _current_active_plan,
    _direct_tool_turn_context,
    _explicit_tool_name_mentions,
    _memory_consolidation_profile_overrides,
    _progress_payload_is_active,
    _single_failed_tool_result_action,
    _stage_task_plan_events,
    _watch_profile_overrides,
    _waiting_without_plan_can_close,
    effective_soft_cap,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_CONFIDENT_COMPLETE,
    ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DECOMPOSE_INVALID,
    ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
    ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
    ADAPTIVE_TERM_DISALLOWED_TOOL,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINALIZATION_BLOCKED,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_JOB_PENDING,
    ADAPTIVE_TERM_LLM_ERROR,
    ADAPTIVE_TERM_NEEDS_USER,
    ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.loop.tools.contracts import (
    CommandExecutionOutcome,
    PrepareOutcome,
    PreparedToolDispatch,
    RawToolResult,
)
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    AdaptiveBudgetConfig,
    BudgetCounters,
    IntentExecutionState,
    ToolCommand,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment
from openminion.modules.brain.trailers import EXPECTED_TRAILERS_METADATA_KEY
from openminion.modules.context.compress.eligibility import (
    DefaultCompactionEligibility,
)
from openminion.modules.llm.schemas import LLMResponse, ToolCall


@dataclass
class _FakeSessionAPI:
    events: list[dict[str, Any]] = field(default_factory=list)
    active_plan: dict[str, Any] | None = None
    raise_type_error_once: bool = False
    raise_exception: bool = False

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": dict(kwargs),
            }
        )

    def get_slice(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if self.raise_exception:
            raise RuntimeError("slice unavailable")
        if self.raise_type_error_once and args:
            self.raise_type_error_once = False
            raise TypeError("keyword-only get_slice")
        return {"active_task_plan": self.active_plan} if self.active_plan else {}


@dataclass
class _FakeSelfCompactionRuntime:
    output_text: str

    def complete(
        self, *, messages, tools, model, tool_choice, max_output_tokens, metadata
    ):
        del messages, tools, tool_choice, max_output_tokens, metadata
        return LLMResponse(
            ok=True,
            provider="fake",
            model=model,
            output_text=self.output_text,
            finish_reason="stop",
            tool_calls=[],
        )


class _FakeCompactionService:
    def __init__(self) -> None:
        self._checker = DefaultCompactionEligibility()
        self.maybe_compact_calls: list[dict[str, Any]] = []

    def evaluate_self_compaction_eligibility(
        self,
        *,
        working_state,
        prompt_token_estimate,
        budget_state,
        now,
    ):
        return self._checker.is_eligible(
            working_state,
            prompt_token_estimate=prompt_token_estimate,
            budget_state=budget_state,
            now=now,
        )

    def maybe_compact_with_state(
        self, session_id: str, *, working_state=None, threshold: int = 5
    ) -> bool:
        self.maybe_compact_calls.append(
            {
                "session_id": session_id,
                "working_state": working_state,
                "threshold": threshold,
            }
        )
        return False


@dataclass
class _FakeServices:
    runner: Any = None
    statuses: list[dict[str, Any]] = field(default_factory=list)
    responses: list[dict[str, Any]] = field(default_factory=list)
    disposition: str = "close"

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs: Any) -> None:
        del state
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result: ActionResult | None = None,
    ) -> Any:
        del logger
        state.status = status
        self.responses.append(
            {"message": message, "status": status, "action_result": action_result}
        )
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, **kwargs: Any) -> str:
        del kwargs
        return ""

    def plan(self, **kwargs: Any) -> None:
        raise AssertionError(f"unexpected plan call: {kwargs!r}")

    def approve_command(self, **kwargs: Any) -> Any:
        return kwargs["command"]

    def act_command(self, **kwargs: Any) -> tuple[ActionResult, None]:
        del kwargs
        return ActionResult(command_id=new_uuid(), status="success", summary="ok"), None

    def assess_plan_feasibility(self, **kwargs: Any) -> None:
        del kwargs
        return

    def evaluate_meta(self, **kwargs: Any) -> None:
        del kwargs
        return

    def apply_meta_directive(self, **kwargs: Any) -> None:
        del kwargs

    def meta_override_response(self, **kwargs: Any) -> None:
        del kwargs
        return

    def meta_tool_restriction_reason(self, **kwargs: Any) -> None:
        del kwargs
        return

    def command_has_side_effects(self, *, command: Any) -> bool:
        del command
        return True

    def resolve_verification_mode(self, *, current: Any, candidate: Any | None) -> Any:
        return candidate if candidate is not None else current

    def verify(self, **kwargs: Any) -> bool:
        del kwargs
        return True

    def improve(self, **kwargs: Any) -> None:
        del kwargs

    def compact(self, **kwargs: Any) -> None:
        del kwargs

    def evaluate_turn_closure(self, **kwargs: Any) -> ClosureJudgment:
        del kwargs
        return ClosureJudgment(satisfied=True, reason="next", next_action="close")

    def apply_closure_judgment(
        self, *, state: WorkingState, judgment: ClosureJudgment
    ) -> str:
        del state, judgment
        return self.disposition

    def extract_success_memories(self, **kwargs: Any) -> list[str]:
        del kwargs
        return ["memory"]

    def create_task(self, **kwargs: Any) -> Any:
        return SimpleNamespace(**kwargs)


@dataclass
class _FakeCommandExecutor:
    outcome: Any | None = None
    calls: list[Any] = field(default_factory=list)
    prepared_calls: list[Any] = field(default_factory=list)

    def execute_command(
        self, *, state: WorkingState, command: Any, logger: Any, **kwargs: Any
    ) -> Any:
        del state, logger, kwargs
        self.calls.append(command)
        return self.outcome or CommandExecutionOutcome(
            approved_command=command,
            action_result=ActionResult(
                command_id=new_uuid(), status="success", summary="ok"
            ),
        )

    def prepare_tool_dispatch(
        self, *, state: WorkingState, command: Any, logger: Any, **kwargs: Any
    ) -> Any:
        del state, logger, kwargs
        self.prepared_calls.append(command)
        return PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=str(getattr(command, "command_id", "") or new_uuid()),
            tool_name=str(getattr(command, "tool_name", "") or ""),
            validated_args=dict(getattr(command, "args", {}) or {}),
            session_id="s-adaptive-char",
            trace_id="trace",
            agent_id="agent",
            lineage={},
            permission_mode="default",
            payload={},
        )

    def execute_prepared_tool_dispatch(
        self, *, prepared_dispatch: PreparedToolDispatch
    ) -> RawToolResult:
        return RawToolResult(
            command_id=prepared_dispatch.command_id,
            tool_name=prepared_dispatch.tool_name,
            raw_output=CommandExecutionOutcome(
                approved_command=prepared_dispatch.approved_command,
                action_result=ActionResult(
                    command_id=new_uuid(), status="success", summary="raw"
                ),
            ),
        )

    def finalize_tool_result(
        self,
        *,
        state: WorkingState,
        prepared_dispatch: PreparedToolDispatch,
        raw_result: RawToolResult,
        logger: Any,
    ) -> CommandExecutionOutcome:
        del state, raw_result, logger
        return CommandExecutionOutcome(
            approved_command=prepared_dispatch.approved_command,
            action_result=ActionResult(
                command_id=new_uuid(), status="success", summary="final"
            ),
        )

    def advance_after_action(self, **kwargs: Any) -> None:
        del kwargs


def _state(**kwargs: Any) -> WorkingState:
    payload: dict[str, Any] = {
        "session_id": "s-adaptive-char",
        "agent_id": "agent",
        "goal": "finish the task",
        "trace_id": "trace",
        "budgets_remaining": BudgetCounters(
            ticks=10,
            tool_calls=10,
            a2a_calls=0,
            tokens=5000,
            time_ms=120000,
        ),
        "llm_calls_max": 10,
    }
    payload.update(kwargs)
    return WorkingState(**payload)


def _ctx(
    *,
    state: WorkingState | None = None,
    decision: Any | None = None,
    services: _FakeServices | None = None,
    command_executor: Any | None = None,
    user_input: str = "run file.read on README.md",
) -> ExecutionContext:
    services = services or _FakeServices()
    if services.runner is None:
        services.runner = SimpleNamespace(
            session_api=_FakeSessionAPI(),
            memory_api=None,
            profile=SimpleNamespace(goal_execution_policy=None),
            tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
            options=SimpleNamespace(failure_strategy="halt"),
        )
    return ExecutionContext(
        state=state or _state(),
        decision=decision
        or SimpleNamespace(
            mode="act_adaptive",
            confidence=0.9,
            reason_code="adaptive_tool_work",
            act_profile="",
            sub_intents=[],
            rationale="",
            question=None,
            answer=None,
            objective="finish",
            success_criteria={},
        ),
        user_input=user_input,
        logger=MagicMock(),
        options=SimpleNamespace(
            profile=None,
            adaptive_budget_config=AdaptiveBudgetConfig(soft_cap=3),
            plan_checkpoint_interval=0,
        ),
        llm_adapter=SimpleNamespace(client=SimpleNamespace()),
        command_executor=command_executor or _FakeCommandExecutor(),
        _services=services,
    )


def _outcome(reason: str, **kwargs: Any) -> AdaptiveToolLoopOutcome:
    return AdaptiveToolLoopOutcome(
        profile_name="general_adaptive_v1",
        mode_name="act_adaptive",
        termination_reason=reason,
        state=kwargs.pop("state", AdaptiveToolLoopState()),
        allowed_tools=frozenset({"file.read"}),
        **kwargs,
    )


def test_helper_branches_and_task_plan_validation_paths() -> None:
    assert effective_soft_cap(None, AdaptiveBudgetConfig(soft_cap=9)) == 9
    assert (
        effective_soft_cap(
            SimpleNamespace(max_steps_hint="bad", sub_intents=object()),
            AdaptiveBudgetConfig(soft_cap=5),
        )
        == 5
    )
    assert (
        effective_soft_cap(
            SimpleNamespace(max_steps_hint=200, sub_intents=["a", "b"]),
            AdaptiveBudgetConfig(soft_cap=5),
        )
        == 128
    )

    assert _active_plan_id(None) == ""
    assert _active_step_ids({"steps": "bad"}) == set()
    assert _append_partial_success(message="", summary="partial") == "partial"
    assert _append_partial_success(message="base", summary="") == "base"
    assert _explicit_tool_name_mentions("file.read, file.read, weather and ???")[
        :2
    ] == (
        "file.read",
        "weather",
    )

    session_api = _FakeSessionAPI(
        active_plan={"plan_id": "p1", "steps": [{"step_id": "s1"}]},
        raise_type_error_once=True,
    )
    services = _FakeServices(runner=SimpleNamespace(session_api=session_api))
    ctx = _ctx(services=services)
    assert _current_active_plan(ctx) == {"plan_id": "p1", "steps": [{"step_id": "s1"}]}
    assert _progress_payload_is_active(
        ctx,
        trailer_type="task_plan.step_completed",
        payload={"plan_id": "p1", "step_id": "s1"},
        active_plan={"plan_id": "p1", "steps": [{"step_id": "s1"}]},
        require_step=True,
    )
    assert not _progress_payload_is_active(
        ctx,
        trailer_type="task_plan.step_completed",
        payload={"plan_id": "missing", "step_id": "s1"},
        active_plan={"plan_id": "p1", "steps": [{"step_id": "s1"}]},
        require_step=True,
    )
    assert not _progress_payload_is_active(
        ctx,
        trailer_type="task_plan.step_completed",
        payload={"plan_id": "p1", "step_id": "unknown"},
        active_plan={"plan_id": "p1", "steps": [{"step_id": "s1"}]},
        require_step=True,
    )
    assert [event["event_type"] for event in session_api.events][-2:] == [
        "task_plan.invalid_trailer",
        "task_plan.invalid_trailer",
    ]


def test_stage_task_plan_events_declares_replaces_and_revises_active_plan() -> None:
    session_api = _FakeSessionAPI(
        active_plan={
            "plan_id": "old-plan",
            "objective": "old objective",
            "steps": [{"step_id": "s1"}],
        }
    )
    ctx = _ctx(services=_FakeServices(runner=SimpleNamespace(session_api=session_api)))
    outcome = _outcome(
        ADAPTIVE_TERM_FINAL_TEXT,
        task_plan={
            "plan_id": "new-plan",
            "objective": "new objective",
            "steps": [{"step_id": "s2"}],
        },
        task_plan_step_completed={"plan_id": "old-plan", "step_id": "s1"},
        task_plan_step_blocked={
            "plan_id": "old-plan",
            "step_id": "s1",
            "reason": "blocked",
        },
        task_plan_abandoned={"plan_id": "old-plan", "reason": "stale"},
        task_plan_completed={"plan_id": "old-plan"},
        task_plan_revision={
            "plan_id": "old-plan",
            "objective": "revised objective",
            "revised_steps": [{"step_id": "s3"}],
            "reason": "updated",
        },
    )

    _stage_task_plan_events(ctx, outcome)

    assert [event["event_type"] for event in session_api.events] == [
        "task_plan.abandoned",
        "task_plan.declared",
        "task_plan.step_completed",
        "task_plan.step_blocked",
        "task_plan.abandoned",
        "task_plan.completed",
        "task_plan.revised",
    ]
    assert session_api.events[-1]["payload"]["plan"]["steps"] == [{"step_id": "s3"}]


def test_profile_overrides_and_metadata_paths() -> None:
    assert _adaptive_loop_metadata(_ctx(), purpose="act")[
        EXPECTED_TRAILERS_METADATA_KEY
    ]
    assert EXPECTED_TRAILERS_METADATA_KEY not in _adaptive_loop_metadata(
        _ctx(), purpose="other"
    )

    assert _watch_profile_overrides(_ctx(state=_state(module_state={}))) is None
    watch_check = _watch_profile_overrides(
        _ctx(
            state=_state(
                module_state={
                    WATCH_MODULE_STATE_KEY: {
                        "enabled": True,
                        "turn_kind": "check",
                        "allowed_tools": ["file.read", "", "time"],
                        "max_iterations": 9,
                    }
                }
            )
        )
    )
    assert watch_check == {
        "turn_kind": "check",
        "allowed_tools": frozenset({"file.read", "time"}),
        "max_iterations": 3,
        "write_authorized": False,
    }
    watch_action = _watch_profile_overrides(
        _ctx(
            state=_state(
                module_state={
                    WATCH_MODULE_STATE_KEY: {
                        "enabled": True,
                        "turn_kind": "action",
                        "max_iterations": 0,
                        "write_authorized": True,
                    }
                }
            )
        )
    )
    assert watch_action["allowed_tools"] == ACT_ADAPTIVE_ALLOWED_TOOLS
    assert watch_action["max_iterations"] == 3
    assert watch_action["write_authorized"] is True

    assert (
        _memory_consolidation_profile_overrides(_ctx(state=_state(module_state={})))
        is None
    )
    consolidation = _memory_consolidation_profile_overrides(
        _ctx(
            state=_state(
                module_state={
                    MEMORY_CONSOLIDATION_MODULE_STATE_KEY: {
                        "enabled": True,
                        "max_iterations": 5,
                        "target_scope": "session",
                    }
                }
            )
        )
    )
    assert consolidation == {
        "allowed_tools": frozenset(),
        "max_iterations": 2,
        "target_scope": "session",
    }


def test_direct_tool_turn_context_covers_seed_parse_mention_and_entry_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = SimpleNamespace(
        reason_code="explicit_tool_command",
        _seeded_commands=[
            ToolCommand(title="read", tool_name="file.read", args={"path": "a"})
        ],
    )
    seeded_ctx = _ctx(decision=seeded)
    assert _direct_tool_turn_context(
        ctx=seeded_ctx, seed_response=None
    ).requested_tool_names == ("file.read",)

    replay_seeded = SimpleNamespace(
        reason_code="explicit_tool_command",
        _seeded_commands=[
            ToolCommand(
                title="write",
                tool_name="file.write",
                args={"path": "README.md", "content": "ok"},
                inputs={
                    "confirmation_source": "policy_replay",
                    "confirmation_grant_id": "grant-123",
                },
            )
        ],
    )
    replay_entry = _direct_tool_turn_context(
        ctx=_ctx(decision=replay_seeded), seed_response=None
    )
    assert replay_entry is not None
    assert getattr(replay_entry.requested_calls[0], "inputs", {}) == {
        "confirmation_source": "policy_replay",
        "confirmation_grant_id": "grant-123",
    }

    monkeypatch.setattr(
        adaptive_context,
        "parse_tool_command",
        lambda **kwargs: ToolCommand(
            title="weather", tool_name="weather", args={"location": "Seoul"}
        ),
    )
    parsed_ctx = _ctx(user_input="weather Seoul")
    assert _direct_tool_turn_context(
        ctx=parsed_ctx, seed_response=None
    ).requested_tool_names == ("weather",)
    monkeypatch.setattr(adaptive_context, "parse_tool_command", lambda **kwargs: None)

    seed_response = SimpleNamespace(
        tool_calls=[ToolCall(name="time", arguments={"timezone": "UTC"})]
    )
    assert (
        _direct_tool_turn_context(
            ctx=_ctx(user_input="please call time"),
            seed_response=seed_response,
        )
        is None
    )
    assert _direct_tool_turn_context(
        ctx=_ctx(user_input="tool time"),
        seed_response=seed_response,
    ).requested_tool_names == ("time",)

    sequence_ctx = _ctx(
        user_input=(
            "tool `web.search`, then `web.fetch`, then `web.fetch` with the "
            "official URLs."
        )
    )
    sequence_entry = _direct_tool_turn_context(ctx=sequence_ctx, seed_response=None)
    assert sequence_entry.requested_tool_names == (
        "web.search",
        "web.fetch",
        "web.fetch",
    )
    assert sequence_entry.match_by_name_only is True

    plain_sequence_ctx = _ctx(
        user_input=(
            "tool exactly one web.search, then web.fetch, then web.fetch "
            "with the official URLs."
        )
    )
    plain_sequence_entry = _direct_tool_turn_context(
        ctx=plain_sequence_ctx,
        seed_response=None,
    )
    assert plain_sequence_entry.requested_tool_names == (
        "web.search",
        "web.fetch",
        "web.fetch",
    )
    assert plain_sequence_entry.match_by_name_only is True

    exact_batch_ctx = _ctx(
        user_input=(
            "Your first tool batch must contain exactly three tool calls: "
            "one web.fetch call, one file.write call for pyproject.toml, and "
            "one file.write call for README.md. Do not call file.read before "
            "these writes."
        )
    )
    exact_batch_entry = _direct_tool_turn_context(
        ctx=exact_batch_ctx,
        seed_response=None,
    )
    assert exact_batch_entry.requested_tool_names == (
        "web.fetch",
        "file.write",
        "file.write",
    )
    assert exact_batch_entry.match_by_name_only is True

    plan_sequence_ctx = _ctx(
        user_input=(
            'tool the `plan` tool with action="declare" and then call the '
            '`plan` tool again with action="complete".'
        )
    )
    plan_sequence_entry = _direct_tool_turn_context(
        ctx=plan_sequence_ctx,
        seed_response=None,
    )
    assert plan_sequence_entry.requested_tool_names == ("plan",)
    assert plan_sequence_entry.match_by_name_only is True

    mention_ctx = _ctx(user_input="please use file.read")
    assert _direct_tool_turn_context(ctx=mention_ctx, seed_response=None) is None
    explicit_mention_ctx = _ctx(user_input="tool file.read")
    assert _direct_tool_turn_context(
        ctx=explicit_mention_ctx, seed_response=None
    ).match_by_name_only

    plain_weather_ctx = _ctx(user_input="hey what's weather today?")
    plain_weather_seed = SimpleNamespace(
        tool_calls=[ToolCall(name="weather", arguments={"location": "san francisco"})]
    )
    assert (
        _direct_tool_turn_context(
            ctx=plain_weather_ctx,
            seed_response=plain_weather_seed,
        )
        is None
    )

    entry_ctx = _ctx(
        decision=SimpleNamespace(reason_code="entry_tool_call"),
        user_input="",
    )
    entry = _direct_tool_turn_context(ctx=entry_ctx, seed_response=seed_response)
    assert entry is None

    explicit_seeded_ctx = _ctx(
        decision=SimpleNamespace(
            reason_code="explicit_tool_command",
            _seeded_commands=[
                ToolCommand(
                    title="Check time",
                    tool_name="time",
                    args={"timezone": "UTC"},
                )
            ],
        ),
        user_input="",
    )
    explicit_entry = _direct_tool_turn_context(
        ctx=explicit_seeded_ctx,
        seed_response=None,
    )
    assert explicit_entry.requested_tool_names == ("time",)

    empty_entry = _direct_tool_turn_context(
        ctx=entry_ctx,
        seed_response=SimpleNamespace(tool_calls=[]),
    )
    assert empty_entry is None


def test_context_adapter_dispatch_fallbacks_and_confirmation_paths() -> None:
    command = ToolCommand(
        title="read", tool_name="file.read", args={"path": "README.md"}
    )
    state = _state(
        intent_execution_states=[
            IntentExecutionState(intent_id="i1", description="read", status="pending")
        ]
    )
    ctx = _ctx(state=state, command_executor=_FakeCommandExecutor())
    adapter = _AdaptiveLoopContextAdapter(ctx)

    outcome = adapter.execute_command(command=command, include_reflect=True)
    assert outcome.action_result.status == "success"
    assert ctx.command_executor.calls[0].tool_name == "file.read"

    prepared = adapter.prepare_tool_dispatch(command=command)
    raw = adapter.execute_prepared_tool_dispatch(prepared_dispatch=prepared)
    finalized = adapter.finalize_tool_result(prepared_dispatch=prepared, raw_result=raw)
    assert finalized.action_result.summary == "final"

    prepare_outcome = PrepareOutcome(
        approved_command=command,
        original_command=command,
        command_id=str(command.command_id),
        tool_name=command.tool_name,
        disposition="prepared",
        action_result=ActionResult(
            command_id=new_uuid(), status="success", summary="prepared"
        ),
    )
    assert (
        adapter.finalize_prepare_outcome(
            prepare_outcome=prepare_outcome
        ).action_result.summary
        == "prepared"
    )

    ask_command = SimpleNamespace(
        kind="ask_user",
        model_copy=lambda **kwargs: SimpleNamespace(kind="ask_user", copied=kwargs),
    )
    adapter._postprocess_outcome(
        SimpleNamespace(
            approved_command=ask_command,
            action_result=ActionResult(
                command_id=new_uuid(), status="success", summary="ask"
            ),
        ),
        original_command=ask_command,
    )
    assert ctx.state.pending_confirmation_command is not None

    confirm = ActionResult(
        command_id=new_uuid(),
        status="needs_user",
        summary="confirm",
        error=ActionError(code="CONFIRM_REQUIRED", message="confirm"),
    )
    approved = ToolCommand(title="write", tool_name="file.write", args={"path": "x"})
    adapter._postprocess_outcome(
        SimpleNamespace(approved_command=approved, action_result=confirm),
        original_command=approved,
    )
    assert "confirm" in ctx.state.post_action_user_message.lower()


def test_execute_builds_profile_variants_and_shortlisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    class _Runtime:
        pass

    monkeypatch.setattr(
        adaptive_modes.DefaultAdaptiveToolLoopLLMRuntime,
        "from_adapter",
        staticmethod(lambda adapter: _Runtime()),
    )
    monkeypatch.setattr(
        adaptive_modes,
        "build_runtime_tool_specs",
        lambda runner, *, allowed_tools: [
            SimpleNamespace(name=name, description=name, input_schema={})
            for name in sorted(allowed_tools)
        ],
    )
    monkeypatch.setattr(adaptive_modes, "resolve_loop_model", lambda ctx: "model")
    monkeypatch.setattr(
        adaptive_modes,
        "should_shortlist_tool_schemas",
        lambda *, profile_name, tool_specs: True,
    )

    def _shortlist(**kwargs: Any) -> Any:
        del kwargs
        return SimpleNamespace(
            llm_call_made=True,
            input_tokens=3,
            output_tokens=4,
            total_tokens=7,
            active_tool_specs=[SimpleNamespace(name="file.read")],
            requestable_tool_specs=[SimpleNamespace(name="tool.request")],
            enabled=True,
            scratchpad_payload=lambda: {"shortlist": True},
        )

    monkeypatch.setattr(adaptive_modes, "shortlist_tool_schemas", _shortlist)
    monkeypatch.setattr(
        adaptive_modes, "_debit_llm_usage", lambda *args, **kwargs: None
    )

    def _run(*args: Any, **kwargs: Any) -> AdaptiveToolLoopOutcome:
        del args
        captured.append(dict(kwargs))
        profile = kwargs["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(profile.allowed_tools or frozenset()),
            mode_result=SimpleNamespace(
                status="done", working_state=_state(), message="mode"
            ),
        )

    monkeypatch.setattr(adaptive, "run_adaptive_tool_loop", _run)

    mode = ActLoopMode()
    mode.apply_mode_config(
        config={
            "max_adaptive_iterations": 2,
            "max_adaptive_tool_calls_per_loop": 3,
            "adaptive_reflection_policy": "always",
        },
        runner=SimpleNamespace(
            options=SimpleNamespace(tool_schema_shortlisting_enabled=True)
        ),
        profile=None,
    )
    result = mode.execute(_ctx())
    assert result.status == "done"
    assert captured[-1]["profile"].profile_name == "general_adaptive_v1"
    assert captured[-1]["requestable_tool_specs"][0].name == "tool.request"
    assert (
        captured[-1]["initial_state"].scratchpad["turn_progress_total_tokens_used"] == 7
    )

    watch_ctx = _ctx(
        state=_state(
            module_state={
                WATCH_MODULE_STATE_KEY: {
                    "enabled": True,
                    "turn_kind": "action",
                    "max_iterations": 1,
                }
            }
        )
    )
    mode.execute(watch_ctx)
    assert captured[-1]["profile"].profile_name == "watch_action_v1"
    assert captured[-1]["profile"].provider_parallel_tool_capacity == 1

    memory_ctx = _ctx(
        state=_state(
            module_state={
                MEMORY_CONSOLIDATION_MODULE_STATE_KEY: {
                    "enabled": True,
                    "target_scope": "agent",
                }
            }
        )
    )
    mode.execute(memory_ctx)
    assert captured[-1]["profile"].profile_name == "memory_consolidation_v1"
    assert captured[-1]["profile"].tool_choice == "none"


def test_direct_tool_turn_adds_dynamic_runtime_tool_to_allowed_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Runtime:
        pass

    monkeypatch.setattr(
        adaptive_modes.DefaultAdaptiveToolLoopLLMRuntime,
        "from_adapter",
        staticmethod(lambda adapter: _Runtime()),
    )

    def _fake_build_specs(runner: Any, *, allowed_tools: frozenset[str]) -> list[Any]:
        del runner
        captured["allowed_tools"] = frozenset(allowed_tools)
        return [
            SimpleNamespace(name=name, description=name, input_schema={})
            for name in sorted(allowed_tools)
        ]

    monkeypatch.setattr(adaptive_modes, "build_runtime_tool_specs", _fake_build_specs)
    monkeypatch.setattr(adaptive_modes, "resolve_loop_model", lambda ctx: "model")

    def _fake_run_loop(*args: Any, **kwargs: Any) -> AdaptiveToolLoopOutcome:
        del args
        captured["tool_specs"] = [spec.name for spec in kwargs["tool_specs"]]
        profile = kwargs["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(profile.allowed_tools or frozenset()),
            mode_result=SimpleNamespace(
                status="done", working_state=_state(), message="mode"
            ),
        )

    monkeypatch.setattr(adaptive, "run_adaptive_tool_loop", _fake_run_loop)

    services = _FakeServices()
    services.runner = SimpleNamespace(
        session_api=_FakeSessionAPI(),
        memory_api=None,
        profile=SimpleNamespace(goal_execution_policy=None),
        tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
        options=SimpleNamespace(failure_strategy="halt"),
        _idempotency_key=lambda **kwargs: "idem",
    )

    result = ActLoopMode().execute(
        _ctx(
            services=services,
            user_input='tool mcp.fixture.echo_text {"text":"hi"}',
        )
    )

    assert result.status == "done"
    assert "mcp.fixture.echo_text" in captured["allowed_tools"]
    assert "mcp.fixture.echo_text" in captured["tool_specs"]


def test_prepare_and_execute_error_and_seeded_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mode = ActLoopMode()

    monkeypatch.setattr(
        adaptive,
        "prepare_coding_profile",
        lambda ctx, *, emit_status_updates=False: SimpleNamespace(mode_result="coding"),
    )
    monkeypatch.setattr(adaptive, "execute_coding_profile", lambda ctx: "coding-result")
    coding_ctx = _ctx(decision=SimpleNamespace(act_profile=BRAIN_ACT_PROFILE_CODING))
    assert mode.prepare(coding_ctx).mode_result == "coding"
    assert mode.execute(coding_ctx) == "coding-result"

    class _Unavailable(Exception):
        pass

    monkeypatch.setattr(
        adaptive_modes, "AdaptiveToolLoopRuntimeUnavailableError", _Unavailable
    )
    monkeypatch.setattr(
        adaptive_modes.DefaultAdaptiveToolLoopLLMRuntime,
        "from_adapter",
        staticmethod(lambda adapter: (_ for _ in ()).throw(_Unavailable("no runtime"))),
    )
    result = mode.prepare(_ctx())
    assert result.mode_result.status == BRAIN_STATE_ERROR
    assert mode.execute(_ctx()).status == BRAIN_STATE_ERROR

    seeded_decision = SimpleNamespace(
        _seeded_commands=[
            ToolCommand(title="read", tool_name="file.read", args={"path": "README.md"})
        ]
    )
    seeded_ctx = _ctx(decision=seeded_decision)
    prep = mode.prepare(seeded_ctx)
    assert prep.consume_user_input_for_command is False

    monkeypatch.setattr(
        adaptive,
        "run_adaptive_tool_loop",
        lambda *args, **kwargs: AdaptiveToolLoopOutcome(
            profile_name="general_seeded_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset({"file.read"}),
            mode_result=SimpleNamespace(
                status="done", working_state=seeded_ctx.state, message="seeded"
            ),
        ),
    )
    assert mode.execute(seeded_ctx).status == "done"


def test_confirmation_replay_seeded_path_gets_recovery_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mode = ActLoopMode()
    seeded_decision = SimpleNamespace(
        reason_code="confirmation_replay",
        _seeded_commands=[
            ToolCommand(
                title="verify",
                tool_name="exec.run",
                args={"command": "python report.py"},
            )
        ],
    )
    seeded_ctx = _ctx(decision=seeded_decision)
    captured: dict[str, Any] = {}

    def _fake_run_loop(*args: Any, **kwargs: Any) -> AdaptiveToolLoopOutcome:
        captured["profile"] = kwargs["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name="general_seeded_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset({"exec.run"}),
            mode_result=SimpleNamespace(
                status="done", working_state=seeded_ctx.state, message="seeded"
            ),
        )

    monkeypatch.setattr(adaptive, "run_adaptive_tool_loop", _fake_run_loop)

    assert mode.execute(seeded_ctx).status == "done"

    profile = captured["profile"]
    assert profile.allow_llm_recovery_after_tool_failure is True
    assert profile.max_iterations == 3
    assert profile.max_tool_calls_per_loop == 3


def test_build_runtime_tool_specs_encode_file_vs_shell_scaffolding_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs = adaptive_modes.build_runtime_tool_specs(
        None,
        allowed_tools=frozenset(
            {"file.write", "exec.run", "host.metrics", "unknown.dynamic"}
        ),
    )
    by_name = {spec.name: spec for spec in specs}

    assert "parent directories" in by_name["file.write"].description
    assert "scaffold" in by_name["file.write"].description.lower()
    assert "platform=" in by_name["exec.run"].description
    assert "shell_family=" in by_name["exec.run"].description
    assert "direct command" in by_name["exec.run"].description
    assert "host.metrics" in by_name["exec.run"].description
    assert "disk usage" in by_name["host.metrics"].description
    assert "unknown.dynamic" not in by_name

    monkeypatch.setattr(
        "openminion.modules.brain.loop.tools.runtime.collect_runtime_tool_schemas",
        lambda runner: [
            {
                "name": "mcp.fixture.echo_text",
                "description": "Echo text",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )
    dynamic_specs = adaptive_modes.build_runtime_tool_specs(
        object(),
        allowed_tools=frozenset({"mcp.fixture.echo_text"}),
    )
    assert [spec.name for spec in dynamic_specs] == ["mcp.fixture.echo_text"]


@pytest.mark.parametrize(
    ("reason", "expected_status"),
    [
        (ADAPTIVE_TERM_DECOMPOSE_INVALID, BRAIN_STATE_ERROR),
        (ADAPTIVE_TERM_NEEDS_USER, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_JOB_PENDING, BRAIN_STATE_JOB_PENDING),
        (ADAPTIVE_TERM_BUDGET_EXHAUSTED, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_ITERATION_CAP, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_CIRCULAR_PATTERN, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_FINALIZATION_BLOCKED, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_FINALIZATION_INCOMPLETE, BRAIN_STATE_WAITING_USER),
        (ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED, BRAIN_STATE_ERROR),
        (ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING, BRAIN_STATE_ERROR),
        (ADAPTIVE_TERM_DISALLOWED_TOOL, BRAIN_STATE_ERROR),
        (ADAPTIVE_TERM_LLM_ERROR, BRAIN_STATE_ERROR),
        (ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY, BRAIN_STATE_ERROR),
        (ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED, BRAIN_STATE_ERROR),
        ("unknown_reason", BRAIN_STATE_ERROR),
    ],
)
def test_result_from_outcome_maps_terminal_reasons(
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    expected_status: str,
) -> None:
    mode = ActLoopMode()
    ctx = _ctx()
    monkeypatch.setattr(
        adaptive_modes,
        "_extract_failure_memories_for_outcome",
        lambda ctx, *, outcome: None,
    )
    outcome = _outcome(
        reason,
        final_text="partial",
        finalization_status={"status": "blocked"},
        error_message="boom",
        action_result=ActionResult(
            command_id=new_uuid(), status="failed", summary="tool failed"
        ),
        state=AdaptiveToolLoopState(
            scratchpad={
                "adaptive.tool_results": [
                    {
                        "ok": False,
                        "error": "tool failed",
                        "error_code": "TOOL_FAILED",
                        "tool_name": "file.read",
                    }
                ]
            }
        ),
    )
    result = mode._result_from_outcome(ctx, outcome=outcome)
    assert result.status == expected_status
    assert result.action_result is not None


def test_result_from_outcome_decompose_handoff_and_missing_subtasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mode = ActLoopMode()
    ctx = _ctx()

    missing = mode._result_from_outcome(
        ctx,
        outcome=_outcome(ADAPTIVE_TERM_DECOMPOSE_REQUESTED),
    )
    assert missing.status == BRAIN_STATE_ERROR

    class _FakeOrchestrateMode:
        def execute(self, ctx: Any) -> Any:
            return SimpleNamespace(status="orchestrated", decision=ctx.decision)

    import openminion.modules.brain.execution.orchestrate.handler as handler

    monkeypatch.setattr(handler, "OrchestrateMode", _FakeOrchestrateMode)
    result = mode._result_from_outcome(
        ctx,
        outcome=_outcome(
            ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
            decompose_subtasks=[{"subtask_id": "s1", "description": "one"}],
        ),
    )
    assert result.status == "orchestrated"
    assert ctx._services.statuses[-1]["mode_state"] == "decompose_handoff"


def test_finalize_success_stages_metadata_and_memory_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_api = _FakeSessionAPI(
        active_plan={"plan_id": "p1", "steps": [{"step_id": "s1"}]}
    )
    memory_api = object()
    services = _FakeServices(
        runner=SimpleNamespace(
            session_api=session_api,
            memory_api=memory_api,
            profile=SimpleNamespace(goal_execution_policy=None),
        )
    )
    ctx = _ctx(services=services)
    staged: dict[str, Any] = {}

    monkeypatch.setattr(
        adaptive_modes,
        "stage_meta_rule_preference",
        lambda runner, *, state, preference: {"candidate_id": "m1"},
    )
    monkeypatch.setattr(
        adaptive_modes,
        "stage_declared_goal",
        lambda runner, *, state, goal: {
            "candidate_id": "g1",
            "skipped_reason": "policy",
        },
    )
    monkeypatch.setattr(
        adaptive_modes,
        "stage_goal_revision",
        lambda runner, *, state, goal_revision: {
            "record_id": "r1",
            "policy_verdict": "allowed",
            "policy_allowed": True,
            "requires_user_confirm": False,
        },
    )
    monkeypatch.setattr(
        adaptive_modes,
        "apply_memory_consolidation_decisions",
        lambda memory_api, *, decisions, target_scope: staged.setdefault(
            "memory",
            {
                "applied_count": 1,
                "promoted_count": 1,
                "discarded_count": 0,
                "deferred_count": 0,
                "errors": ["e1"],
                "target_scope": target_scope,
            },
        ),
    )

    outcome = _outcome(
        ADAPTIVE_TERM_CONFIDENT_COMPLETE,
        final_text="done",
        pending_turn_context={
            "original_user_request": "finish the task",
            "active_work_summary": "next",
            "known_context": {"reason": "carry"},
        },
        session_work_summary="summary",
        meta_rule_preference={
            "rule": "preferred_test_depth",
            "preferred_value": "high",
            "reasoning": "quality",
        },
        goal_declaration={
            "goal": "ship",
            "trigger": "tests passed",
            "action_type": "task",
        },
        goal_revision={
            "goal_id": "g1",
            "previous_goal": "ship",
            "goal": "ship safely",
            "trigger": "new info",
        },
        memory_consolidation_decisions=[{"kind": "promote", "content": "fact"}],
        state=AdaptiveToolLoopState(),
    )
    result = ActLoopMode()._finalize_success(
        ctx,
        loop_outcome=outcome,
        runtime=_FakeSelfCompactionRuntime(output_text=""),
        model="gpt-4.2-mini",
    )
    assert result.status == BRAIN_STATE_DONE
    assert ctx.state.pending_turn_context is not None
    assert ctx.state.session_work_summary == "summary"
    assert result.action_result.outputs["meta_rule_preference.candidate_id"] == "m1"
    assert result.action_result.outputs["goal_declaration.candidate_id"] == "g1"
    assert result.action_result.outputs["goal_revision.record_id"] == "r1"
    assert result.action_result.outputs["memory_consolidation.applied_count"] == 1


def test_finalize_success_runs_self_compaction_after_consolidation_marker() -> None:
    session_api = _FakeSessionAPI()
    compaction_service = _FakeCompactionService()
    services = _FakeServices(
        runner=SimpleNamespace(
            session_api=session_api,
            memory_api=None,
            context_api=SimpleNamespace(service=compaction_service),
            profile=SimpleNamespace(goal_execution_policy=None),
        )
    )
    state = _state()
    state.budgets_remaining.tokens = 100
    state.module_state = {
        "memory_context_maintenance": {
            "last_consolidation_marker": "2026-05-22T11:59:59+00:00",
        }
    }
    ctx = _ctx(services=services, state=state, user_input=("checkpoint " * 90).strip())
    outcome = _outcome(
        ADAPTIVE_TERM_CONFIDENT_COMPLETE,
        final_text=("checkpoint " * 90).strip(),
        state=AdaptiveToolLoopState(),
    )

    result = ActLoopMode()._finalize_success(
        ctx,
        loop_outcome=outcome,
        runtime=_FakeSelfCompactionRuntime(
            output_text="Finished extraction and marker wiring. Next: land the smoke test."
        ),
        model="gpt-4.2-mini",
    )

    maintenance = ctx.state.module_state["memory_context_maintenance"]
    assert result.status == BRAIN_STATE_DONE
    assert (
        ctx.state.session_work_summary
        == "Finished extraction and marker wiring. Next: land the smoke test."
    )
    assert (
        maintenance["last_consolidation_marker"] < maintenance["last_compaction_marker"]
    )
    assert result.action_result.outputs["self_compaction.applied"] is True
    assert result.action_result.outputs["self_compaction.reason_code"] == "OK"
    assert session_api.events[-1]["event_type"] == "context.self_compaction"
    assert compaction_service.maybe_compact_calls


def test_finalize_success_self_compaction_is_idempotent_for_same_state_hash() -> None:
    session_api = _FakeSessionAPI()
    compaction_service = _FakeCompactionService()
    services = _FakeServices(
        runner=SimpleNamespace(
            session_api=session_api,
            memory_api=None,
            context_api=SimpleNamespace(service=compaction_service),
            profile=SimpleNamespace(goal_execution_policy=None),
        )
    )
    state = _state()
    state.budgets_remaining.tokens = 100
    ctx = _ctx(services=services, state=state, user_input=("checkpoint " * 90).strip())
    outcome = _outcome(
        ADAPTIVE_TERM_CONFIDENT_COMPLETE,
        final_text=("checkpoint " * 90).strip(),
        state=AdaptiveToolLoopState(),
    )
    mode = ActLoopMode()

    first = mode._finalize_success(
        ctx,
        loop_outcome=outcome,
        runtime=_FakeSelfCompactionRuntime(output_text="Checkpoint 1"),
        model="gpt-4.2-mini",
    )
    second = mode._finalize_success(
        ctx,
        loop_outcome=outcome,
        runtime=_FakeSelfCompactionRuntime(output_text="Checkpoint 2"),
        model="gpt-4.2-mini",
    )

    assert first.action_result.outputs["self_compaction.reason_code"] == "OK"
    assert (
        second.action_result.outputs["self_compaction.reason_code"]
        == "ALREADY_COMPACTED_THIS_TURN"
    )
    assert ctx.state.session_work_summary == "Checkpoint 1"


def test_finalize_seeded_success_disposition_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mode = ActLoopMode()
    monkeypatch.setattr(adaptive_modes, "transition", lambda *args, **kwargs: None)
    for disposition, expected_status in [
        ("close", BRAIN_STATE_DONE),
        ("continue", BRAIN_STATE_WAITING_USER),
        ("replan", BRAIN_STATE_DONE),
        ("ask_user", BRAIN_STATE_WAITING_USER),
    ]:
        services = _FakeServices(disposition=disposition)
        ctx = _ctx(services=services, state=_state(status=BRAIN_STATE_DONE))
        outcome = _outcome(
            ADAPTIVE_TERM_FINAL_TEXT,
            final_text="done",
            action_result=ActionResult(
                command_id=new_uuid(), status="success", summary="ok"
            ),
        )
        result = mode._finalize_seeded_success(ctx, loop_outcome=outcome)
        assert result.status == expected_status


def test_single_failed_tool_and_waiting_helpers() -> None:
    assert (
        _single_failed_tool_result_action(
            _outcome(
                ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
                error_message="fallback",
                state=AdaptiveToolLoopState(
                    scratchpad={
                        "adaptive.tool_results": [
                            {
                                "ok": False,
                                "content": "bad",
                                "data": {"path": "x"},
                                "tool_name": "file.read",
                            }
                        ]
                    }
                ),
            )
        ).error.details["tool_name"]
        == "file.read"
    )
    assert (
        _single_failed_tool_result_action(
            _outcome(
                ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
                state=AdaptiveToolLoopState(
                    scratchpad={"adaptive.tool_results": [{"ok": True}]}
                ),
            )
        )
        is None
    )

    ctx = _ctx(
        state=_state(
            status=BRAIN_STATE_WAITING_USER,
            post_action_user_message="I no longer have an active plan for that result.",
        )
    )
    assert _waiting_without_plan_can_close(ctx=ctx, remaining_ids=[])
