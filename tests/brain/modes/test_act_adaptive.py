from __future__ import annotations

from dataclasses import dataclass, field
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from openminion.modules.brain.loop.adaptive import (
    ACT_ADAPTIVE_ALLOWED_TOOLS,
    ActLoopMode,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_FINALIZATION_BLOCKED,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.loop.tools.snapshot import (
    LoopSnapshot,
    LoopToolCallRecord,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.schemas import (
    ActionError,
    ActionResult,
    BudgetCounters,
    IntentExecutionState,
    ToolCommand,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment
from openminion.modules.brain.schemas.plan import Plan
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, ToolCall, UsageInfo
from openminion.modules.brain.execution.advance import advance_after_action
from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_STATE_WAITING_USER,
)


@dataclass
class _FakeLLMClient:
    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def complete(self, messages, tools=None, **overrides) -> LLMResponse:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "overrides": dict(overrides),
            }
        )
        response = self.responses[self._index]
        self._index += 1
        return response


@dataclass
class _FakeCommandExecutor:
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)
    include_reflect_values: list[bool] = field(default_factory=list)
    delays_by_path: dict[str, float] = field(default_factory=dict)
    call_windows: list[tuple[str, float, float]] = field(default_factory=list)
    _index: int = 0

    def execute_command(
        self,
        *,
        state: WorkingState,
        command: Any,
        logger: Any,
        preapproved: bool = False,
        approve_only: bool = False,
        include_reflect: bool = True,
    ) -> CommandExecutionOutcome:
        del state, logger, preapproved, approve_only
        self.calls.append(command)
        self.include_reflect_values.append(include_reflect)
        path = str(getattr(command, "args", {}).get("path", "") or "")
        started = time.monotonic()
        delay = float(self.delays_by_path.get(path, 0.0) or 0.0)
        if delay > 0:
            time.sleep(delay)
        outcome = self.outcomes[self._index]
        self._index += 1
        finished = time.monotonic()
        self.call_windows.append((path, started, finished))
        return outcome


@dataclass
class _FakeServices:
    statuses: list[dict[str, Any]] = field(default_factory=list)
    runner: Any = None
    closure_judgment: ClosureJudgment | None = None
    closure_disposition: str | None = None

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
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
        kind: str = "assistant",
    ) -> Any:
        del logger, kind
        state.status = status
        return SimpleNamespace(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=action_result,
        )

    def direct_response(self, *, user_input, decision):
        del user_input, decision
        return ""

    def plan(self, **kwargs):
        raise AssertionError("act_adaptive should not call plan()")

    def approve_command(self, *, state, command, logger):
        del state, logger
        return command

    def act_command(self, *, state, command, logger):
        del state, logger
        return ActionResult(command_id=new_uuid(), status="success", summary="ok"), None

    def assess_plan_feasibility(self, **kwargs):
        del kwargs
        return

    def evaluate_meta(self, **kwargs):
        del kwargs
        return

    def apply_meta_directive(self, **kwargs):
        del kwargs

    def meta_override_response(self, **kwargs):
        del kwargs
        return

    def meta_tool_restriction_reason(self, **kwargs):
        del kwargs
        return

    def command_has_side_effects(self, *, command):
        del command
        return True

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, **kwargs):
        del kwargs
        return True

    def improve(self, **kwargs):
        del kwargs

    def compact(self, **kwargs):
        del kwargs

    def evaluate_turn_closure(self, **kwargs) -> ClosureJudgment:
        del kwargs
        if self.closure_judgment is not None:
            return self.closure_judgment
        return ClosureJudgment(satisfied=True, next_action="close")

    def apply_closure_judgment(self, *, state, judgment) -> str:
        del state, judgment
        if self.closure_disposition is not None:
            return self.closure_disposition
        return "close"

    def extract_success_memories(self, **kwargs):
        del kwargs
        return []


def _state(tool_calls: int = 8) -> WorkingState:
    return WorkingState(
        session_id="s-act-adaptive",
        agent_id="agent",
        goal="inspect workspace and summarize",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=tool_calls,
            a2a_calls=0,
            tokens=5000,
            time_ms=120000,
        ),
        llm_calls_max=10,
    )


def _ctx(
    llm_client: _FakeLLMClient,
    executor: _FakeCommandExecutor,
    *,
    services: _FakeServices | None = None,
    state: WorkingState | None = None,
) -> tuple[ExecutionContext, _FakeServices]:
    services = services or _FakeServices()
    ctx = ExecutionContext(
        state=state or _state(),
        decision=SimpleNamespace(
            mode="act_adaptive",
            confidence=0.9,
            reason_code="adaptive_tool_work",
            sub_intents=[],
            rationale="",
            question=None,
            answer=None,
            objective="inspect workspace",
            success_criteria={},
        ),
        user_input="inspect the repo and summarize auth files",
        logger=MagicMock(),
        options=SimpleNamespace(profile=None),
        llm_adapter=SimpleNamespace(client=llm_client),
        command_executor=executor,
        _services=services,
    )
    return ctx, services


def test_general_adaptive_profile_allows_decompose_control_tool() -> None:
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)
    captured: dict[str, Any] = {}

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["profile"] = kwargs.get("profile")
        captured["tool_specs"] = list(kwargs.get("tool_specs") or [])
        profile = captured["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=str(getattr(profile, "profile_name", "") or ""),
            mode_name=str(getattr(profile, "mode_name", "") or ""),
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(getattr(profile, "allowed_tools", frozenset())),
            mode_result=SimpleNamespace(
                status="done",
                working_state=ctx.state,
                message="ok",
            ),
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    profile = captured["profile"]
    assert getattr(profile, "profile_name", "") == "general_adaptive_v1"
    assert "decompose" in set(getattr(profile, "allowed_tools", frozenset()))
    assert "decompose" in {
        str(getattr(spec, "name", "") or "").strip()
        for spec in list(captured["tool_specs"] or [])
    }


def test_general_adaptive_honors_explicit_control_tool_opt_out() -> None:
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)
    ctx.user_input = (
        "Inspect the workspace and research current docs without "
        "plan/decompose/git/pip/tool.list. Do the work directly in this turn."
    )
    captured: dict[str, Any] = {}

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["profile"] = kwargs.get("profile")
        captured["tool_specs"] = list(kwargs.get("tool_specs") or [])
        profile = captured["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=str(getattr(profile, "profile_name", "") or ""),
            mode_name=str(getattr(profile, "mode_name", "") or ""),
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(getattr(profile, "allowed_tools", frozenset())),
            mode_result=SimpleNamespace(
                status="done",
                working_state=ctx.state,
                message="ok",
            ),
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    profile = captured["profile"]
    assert getattr(profile, "profile_name", "") == "general_adaptive_v1"
    assert bool(getattr(profile, "allow_plan_tool", True)) is False
    profile_tools = set(getattr(profile, "allowed_tools", frozenset()))
    tool_names = {
        str(getattr(spec, "name", "") or "").strip()
        for spec in list(captured["tool_specs"] or [])
    }
    assert "decompose" not in profile_tools
    assert not any(tool.startswith("plan.") for tool in profile_tools)
    assert "tool.list" not in profile_tools
    assert not any(tool.startswith("git.") for tool in profile_tools)
    assert "decompose" not in tool_names
    assert not any(tool.startswith("plan.") for tool in tool_names)
    assert "tool.list" not in tool_names
    assert not any(tool.startswith("git.") for tool in tool_names)
    assert "file.list_dir" in tool_names
    assert "web.search" in tool_names


def test_research_child_general_adaptive_does_not_expose_decompose() -> None:
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)
    ctx.decision.reason_code = "research_iteration_fallback"
    captured: dict[str, Any] = {}

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["profile"] = kwargs.get("profile")
        captured["tool_specs"] = list(kwargs.get("tool_specs") or [])
        profile = captured["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=str(getattr(profile, "profile_name", "") or ""),
            mode_name=str(getattr(profile, "mode_name", "") or ""),
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(getattr(profile, "allowed_tools", frozenset())),
            mode_result=SimpleNamespace(
                status="done",
                working_state=ctx.state,
                message="ok",
            ),
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    profile = captured["profile"]
    assert getattr(profile, "profile_name", "") == "general_adaptive_v1"
    assert bool(getattr(profile, "allow_plan_tool", True)) is False
    assert "decompose" not in set(getattr(profile, "allowed_tools", frozenset()))
    tool_names = {
        str(getattr(spec, "name", "") or "").strip()
        for spec in list(captured["tool_specs"] or [])
    }
    assert "decompose" not in tool_names
    assert "web.search" in tool_names
    assert "web.fetch" in tool_names


def test_seeded_confirmation_replay_does_not_expose_plan_control_tools() -> None:
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)
    ctx.decision.reason_code = "confirmation_replay_validation"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]
    captured: dict[str, Any] = {}

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["profile"] = kwargs.get("profile")
        captured["tool_specs"] = list(kwargs.get("tool_specs") or [])
        profile = captured["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=str(getattr(profile, "profile_name", "") or ""),
            mode_name=str(getattr(profile, "mode_name", "") or ""),
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(getattr(profile, "allowed_tools", frozenset())),
            mode_result=SimpleNamespace(
                status="done",
                working_state=ctx.state,
                message="ok",
            ),
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    profile_tools = set(getattr(captured["profile"], "allowed_tools", frozenset()))
    tool_names = {
        str(getattr(spec, "name", "") or "").strip()
        for spec in list(captured["tool_specs"] or [])
    }
    assert "decompose" not in profile_tools
    assert not any(tool.startswith("plan.") for tool in profile_tools)
    assert "decompose" not in tool_names
    assert not any(tool.startswith("plan.") for tool in tool_names)
    assert "file.write" in profile_tools
    assert "web.search" in tool_names


def _failure_outcome(reason: str) -> AdaptiveToolLoopOutcome:
    return AdaptiveToolLoopOutcome(
        profile_name="adaptive-test",
        mode_name="act_adaptive",
        termination_reason=reason,
        state=AdaptiveToolLoopState(
            iteration=2,
            scratchpad={
                "adaptive.tool_results": [
                    {
                        "tool_name": "web.search",
                        "args_signature": '{"query":"sf weather"}',
                    }
                ],
                "correction_history": [
                    {
                        "iteration_index": 1,
                        "correction_type": "retry_same",
                        "diagnosis_summary": "auth missing",
                        "applied": True,
                    }
                ],
            },
        ),
        allowed_tools=frozenset({"web.search"}),
        action_result=ActionResult(
            command_id="cmd-1",
            status="failed",
            summary="web.search failed",
            error=ActionError(code="AUTH_REQUIRED", message="auth missing"),
        ),
        error_message="web.search failed",
    )


def test_act_adaptive_mode_is_registered_and_has_distinct_description() -> None:
    handler = ActLoopMode()

    assert handler.mode_name == "act_loop_adaptive"
    assert "shared same-turn act loop" in handler.mode_description
    assert "prior tool output" in handler.mode_description
    assert "web.search" in ACT_ADAPTIVE_ALLOWED_TOOLS


def test_failure_terminations_trigger_failure_memory_extraction() -> None:
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    services = _FakeServices(runner=SimpleNamespace(tool_api=None))
    ctx, _ = _ctx(llm_client, executor, services=services)
    mode = ActLoopMode()

    for reason in (
        ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ADAPTIVE_TERM_ITERATION_CAP,
        ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ):
        with patch(
            "openminion.modules.brain.execution.memory.extract_failure_memories"
        ) as extract_mock:
            mode._result_from_outcome(ctx, outcome=_failure_outcome(reason))
            extract_mock.assert_called_once()
            assert extract_mock.call_args.kwargs["termination_reason"] == reason


def test_budget_exhausted_does_not_trigger_failure_memory_extraction() -> None:
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    services = _FakeServices(runner=SimpleNamespace(tool_api=None))
    ctx, _ = _ctx(llm_client, executor, services=services)
    mode = ActLoopMode()

    with patch(
        "openminion.modules.brain.execution.memory.extract_failure_memories"
    ) as extract_mock:
        mode._result_from_outcome(ctx, outcome=_failure_outcome("budget_exhausted"))
        extract_mock.assert_not_called()


def test_budget_exhausted_fail_closes_when_typed_finalization_is_required() -> None:
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)
    outcome = _failure_outcome("budget_exhausted")
    outcome.profile_name = "general_adaptive_v1"
    outcome.state.scratchpad["adaptive.tool_results"] = [
        {"tool_name": "web.search", "ok": True, "content": "search ok"},
        {"tool_name": "web.fetch", "ok": True, "content": "fetch ok"},
        {"tool_name": "web.fetch", "ok": True, "content": "fetch ok 2"},
    ]

    result = ActLoopMode()._result_from_outcome(ctx, outcome=outcome)

    assert result.status == "error"
    assert "required typed finalization_status contract" in str(result.message or "")
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "act_finalization_contract_missing"


def test_act_adaptive_circular_pattern_returns_truthful_waiting_user_result() -> None:
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=_failure_outcome(ADAPTIVE_TERM_CIRCULAR_PATTERN),
    )

    assert result.status == "waiting_user"
    assert "repeated the same tool pattern" in str(result.message or "")
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "act_adaptive_circular_pattern"


def test_act_adaptive_changed_tool_arguments_reach_normal_finalization() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c1", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "b.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c3", name="file.read", arguments={"path": "c.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The files share the same structure and no more tool calls are needed.",
                finish_reason="stop",
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read a.py",
                    outputs={"path": "a.py"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read b.py",
                    outputs={"path": "b.py"},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read c.py",
                    outputs={"path": "c.py"},
                ),
            ),
        ]
    )
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.working_state.status == "done"
    assert "no more tool calls are needed" in str(result.message or "").lower()
    assert len(llm_client.calls) == 4
    assert llm_client.calls[-1]["overrides"]["tool_choice"] == "auto"


def test_act_adaptive_correction_budget_exhausted_returns_truthful_waiting_user_result() -> (
    None
):
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=_failure_outcome(ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED),
    )

    assert result.status == "waiting_user"
    assert "exhausted its correction budget" in str(result.message or "")
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == (
        "act_adaptive_correction_budget_exhausted"
    )


def test_act_adaptive_final_text_fail_closes_without_typed_finalization_contract() -> (
    None
):
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=AdaptiveToolLoopState(
                scratchpad={
                    "adaptive.tool_results": [
                        {"tool_name": "web.search", "ok": True, "content": "search"},
                        {"tool_name": "web.fetch", "ok": True, "content": "uv"},
                        {"tool_name": "web.fetch", "ok": True, "content": "pipx"},
                    ]
                }
            ),
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            final_text="## PLAN\n1. search\n2. fetch\n3. compare",
        ),
    )

    assert result.status == "error"
    assert "required typed finalization_status contract" in str(result.message or "")
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "act_finalization_contract_missing"


def test_act_adaptive_direct_tool_closure_failed_returns_specific_error() -> None:
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset({"file.read"}),
            error_message=(
                "Answer-only closure returned more tool calls after the requested "
                "direct-tool batch had already completed."
            ),
        ),
    )

    assert result.status == "error"
    assert "Answer-only closure returned more tool calls" in str(result.message or "")
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == (
        "act_adaptive_direct_tool_closure_failed"
    )


def test_act_adaptive_executes_multi_round_same_turn_tool_work() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.list_dir",
                        arguments={"path": "src/openminion/modules/brain/modes"},
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.read",
                        arguments={
                            "path": "src/openminion/modules/brain/modes/coding/handler.py"
                        },
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The coding handler owns the coding-mode loop.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Tool-backed inspection complete.",
                },
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed mode files",
                    outputs={"entries": ["coding", "plan", "respond"]},
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read coding handler",
                    outputs={"content": "class CodingMode"},
                ),
            ),
        ]
    )
    ctx, services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.working_state.status == "done"
    assert "coding handler" in str(result.message or "").lower()
    assert [call.tool_name for call in executor.calls] == ["file.list_dir", "file.read"]
    assert len(llm_client.calls) == 3
    assert any(
        (item.get("payload") or {}).get("adaptive.profile") == "general_adaptive_v1"
        for item in services.statuses
    )
    assert any(
        (item.get("payload") or {}).get("adaptive.tool_calls_total") == 2
        for item in services.statuses
    )
    second_round_messages = llm_client.calls[1]["messages"]
    assert any(message.role == "tool" for message in second_round_messages)


def test_act_adaptive_honors_confident_complete_trailer() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=(
                    "Workspace inspection complete."
                    "\n<confident_complete>"
                    '{"complete": true, "reasoning": "Reviewed enough evidence."}'
                    "</confident_complete>"
                ),
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The model declares the act-route answer complete.",
                },
                finish_reason="stop",
            )
        ]
    )
    executor = _FakeCommandExecutor(outcomes=[])
    ctx, services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert str(result.message or "") == "Workspace inspection complete."
    assert result.action_result is not None
    assert result.action_result.outputs["adaptive.termination_reason"] == "final_text"
    assert (
        result.action_result.outputs["adaptive.confident_complete_reasoning"]
        == "Reviewed enough evidence."
    )
    first_call_messages = llm_client.calls[0]["messages"]
    assert any(
        message.role == "system" and "confident_complete" in message.content
        for message in first_call_messages
    )
    status_payloads = [item.get("payload") or {} for item in services.statuses]
    assert any(
        item.get("adaptive.termination_reason") == "final_text"
        for item in status_payloads
    )


def test_act_adaptive_records_watch_outcome_payload() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Deployment is unhealthy.",
                watch_outcome={
                    "condition_met": True,
                    "summary": "Deployment is unhealthy.",
                },
                finish_reason="stop",
            )
        ]
    )
    executor = _FakeCommandExecutor(outcomes=[])
    ctx, _services = _ctx(llm_client, executor)
    ctx.state.module_state = {
        "watch_subscription": {
            "enabled": True,
            "allowed_tools": ["web.fetch", "time"],
            "max_iterations": 3,
        }
    }

    result = ActLoopMode().execute(ctx)
    assert result.status == "done"
    assert str(result.message or "") == "Deployment is unhealthy."
    assert result.action_result is not None
    assert result.action_result.outputs["watch.condition_met"] is True
    assert result.action_result.outputs["watch.summary"] == "Deployment is unhealthy."


def test_act_adaptive_incomplete_finalization_status_returns_truthful_blocked_result() -> (
    None
):
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
            state=AdaptiveToolLoopState(
                scratchpad={
                    "adaptive.tool_results": [
                        {"tool_name": "web.search", "ok": True},
                        {"tool_name": "web.fetch", "ok": True},
                        {"tool_name": "web.fetch", "ok": True},
                    ]
                }
            ),
            allowed_tools=frozenset({"web.search", "web.fetch"}),
            final_text=(
                "I gathered the sources, but I still need one more official "
                "document before I can produce the final comparison table."
            ),
            finalization_status={
                "status": "incomplete",
                "reasoning": "Need one more official source.",
                "remaining_work": "Fetch one additional primary source.",
                "blocking_reason": "",
            },
        ),
    )

    assert result.status == "waiting_user"
    assert (
        result.message
        == "I gathered the sources, but I still need one more official document before I can produce the final comparison table."
    )
    assert result.action_result is not None
    assert result.action_result.outputs["adaptive.termination_reason"] == (
        ADAPTIVE_TERM_FINALIZATION_INCOMPLETE
    )


def test_act_adaptive_finalization_contract_missing_surfaces_single_failed_tool_result() -> (
    None
):
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
            state=AdaptiveToolLoopState(
                scratchpad={
                    "adaptive.tool_results": [
                        {
                            "tool_name": "file.read",
                            "ok": False,
                            "content": "path does not exist: /repo/missing.txt",
                            "error": "path does not exist: /repo/missing.txt",
                            "error_code": "NOT_FOUND",
                            "data": {
                                "error_code": "NOT_FOUND",
                                "error_details": {"path": "/repo/missing.txt"},
                            },
                        }
                    ]
                }
            ),
            allowed_tools=frozenset({"file.read"}),
            error_message="General act work ended without the required typed finalization_status contract.",
        ),
    )

    assert result.status == "error"
    assert result.message == "path does not exist: /repo/missing.txt"
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "NOT_FOUND"
    assert result.action_result.error.details["tool_name"] == "file.read"


def test_act_adaptive_finalization_contract_missing_closes_from_successful_tool_evidence() -> (
    None
):
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer="Here is the comparison summary built from the gathered sources.",
        ),
        closure_disposition="close",
    )
    ctx, _services = _ctx(llm_client, executor, services=services)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
            state=AdaptiveToolLoopState(
                scratchpad={
                    "adaptive.tool_results": [
                        {
                            "tool_name": "web.search",
                            "ok": True,
                            "content": "source list",
                            "data": {"hits": ["source-a", "source-b"]},
                        }
                    ]
                }
            ),
            allowed_tools=frozenset({"web.search"}),
            error_message="General act work ended without the required typed finalization_status contract.",
        ),
    )

    assert result.status == "done"
    assert "comparison summary" in str(result.message or "")
    assert result.action_result is not None
    assert result.action_result.error is None


def test_act_adaptive_requires_typed_finalization_after_substantive_tool_work() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.read",
                        arguments={"path": "README.md"},
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Done. Completed successfully.",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Still done; the tool output was enough.",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Still done; the tool output was enough.",
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read README",
                    outputs={"content": "# OpenMinion"},
                ),
            ),
        ]
    )
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "error"
    assert result.action_result is not None
    assert result.action_result.error is not None
    assert result.action_result.error.code == "act_finalization_contract_missing"
    assert len(llm_client.calls) == 4
    retry_messages = llm_client.calls[2]["messages"]
    assert any(
        "structured finalization_status signal"
        in str(getattr(message, "content", "") or "")
        for message in retry_messages
        if getattr(message, "role", "") == "system"
    )


def test_act_adaptive_salvages_typed_finalization_with_status_only_follow_up() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.read",
                        arguments={"path": "README.md"},
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Done. Completed successfully.",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Still done; the tool output was enough.",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The already-provided answer completed the request.",
                },
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="read README",
                    outputs={"content": "# OpenMinion"},
                ),
            ),
        ]
    )
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.message == "Still done; the tool output was enough."
    assert result.action_result is not None
    assert result.action_result.outputs["adaptive.finalization_status"]["status"] == (
        "final_answer"
    )
    assert llm_client.calls[-1]["overrides"]["tool_choice"] == "none"
    assert llm_client.calls[-1]["tools"] == []


def test_act_adaptive_blocked_finalization_status_returns_truthful_blocked_result() -> (
    None
):
    llm_client = _FakeLLMClient(responses=[])
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode()._result_from_outcome(
        ctx,
        outcome=AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            termination_reason=ADAPTIVE_TERM_FINALIZATION_BLOCKED,
            state=AdaptiveToolLoopState(
                scratchpad={
                    "adaptive.tool_results": [
                        {"tool_name": "file.read", "ok": False},
                    ]
                }
            ),
            allowed_tools=frozenset({"file.read"}),
            final_text="I could not read the required file, so the task is blocked.",
            finalization_status={
                "status": "blocked",
                "reasoning": "Required file read failed.",
                "remaining_work": "",
                "blocking_reason": "File read failed.",
            },
        ),
    )

    assert result.status == "waiting_user"
    assert result.action_result is not None
    assert result.action_result.outputs["adaptive.termination_reason"] == (
        ADAPTIVE_TERM_FINALIZATION_BLOCKED
    )
    assert result.action_result.outputs["adaptive.finalization_status"]["status"] == (
        "blocked"
    )


def test_act_adaptive_carries_turn_progress_into_statuses_and_final_outputs() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="call-1", name="location", arguments={}),
                ],
                usage=UsageInfo(input_tokens=400, output_tokens=400, total_tokens=800),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Done.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Location result summarized.",
                },
                usage=UsageInfo(
                    input_tokens=300,
                    output_tokens=400,
                    total_tokens=700,
                ),
                finish_reason="stop",
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="location ok",
                    outputs={"city": "Seattle"},
                ),
            )
        ]
    )
    ctx, services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.action_result is not None
    assert result.action_result.outputs["total_tokens_used"] == 1500
    assert result.action_result.outputs["tool_calls_count"] == 1
    status_payloads = [item.get("payload") or {} for item in services.statuses]
    assert any(
        payload.get("turn.llm_call_count") == 1
        and payload.get("total_tokens_used") == 800
        and payload.get("turn.tool_name") == "location"
        for payload in status_payloads
    )


def test_act_adaptive_uses_action_profile_for_watch_triggered_actions() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Restarted the deployment.",
                finish_reason="stop",
            )
        ]
    )
    executor = _FakeCommandExecutor(outcomes=[])
    ctx, _services = _ctx(llm_client, executor)
    ctx.state.module_state = {
        "watch_subscription": {
            "enabled": True,
            "turn_kind": "action",
            "max_iterations": 3,
        }
    }

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert str(result.message or "") == "Restarted the deployment."
    assert result.action_result is not None
    assert "watch.condition_met" not in result.action_result.outputs


def test_act_adaptive_records_session_work_summary_and_updates_state() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=(
                    "Implemented authentication flow."
                    "\n<session_work_summary>"
                    '{"summary": "Built authentication flow in auth.py, added login tests, and still need to wire token refresh."}'
                    "</session_work_summary>"
                ),
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The model declares the act-route answer complete.",
                },
                finish_reason="stop",
            )
        ]
    )
    executor = _FakeCommandExecutor(outcomes=[])
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert str(result.message or "") == "Implemented authentication flow."
    assert result.action_result is not None
    assert (
        result.action_result.outputs["session_work_summary"]
        == "Built authentication flow in auth.py, added login tests, and still need to wire token refresh."
    )
    assert (
        ctx.state.session_work_summary
        == "Built authentication flow in auth.py, added login tests, and still need to wire token refresh."
    )


def test_act_adaptive_records_pending_turn_context_and_updates_state() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=(
                    "I found Oakland from your IP. Would you like me to get the current weather in Oakland for you?"
                    "\n<pending_turn_context>"
                    '{"original_user_request": "tell me your location like ip and city?", '
                    '"active_work_summary": "If the user agrees, provide current weather for Oakland.", '
                    '"known_context": {"location": "Oakland", "region": "California"}, '
                    '"missing_fields": [], "artifact_refs": [], "response_preferences": {}}'
                    "</pending_turn_context>"
                ),
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The model declares the act-route answer complete.",
                },
                finish_reason="stop",
            )
        ]
    )
    executor = _FakeCommandExecutor(outcomes=[])
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.action_result is not None
    assert result.action_result.outputs["pending_turn_context"]["known_context"] == {
        "location": "Oakland",
        "region": "California",
    }
    assert ctx.state.pending_turn_context is not None
    assert ctx.state.pending_turn_context.active_work_summary == (
        "If the user agrees, provide current weather for Oakland."
    )
    assert ctx.state.pending_turn_context_stale_turns == 0


def test_act_adaptive_confirmation_required_message_uses_command_metadata() -> None:
    command = ToolCommand(
        title="Write server file",
        tool_name="file.write",
        args={"path": "server.asm", "content": "section .text", "cwd": "/tmp/app"},
        success_criteria={"path": "server.asm"},
        risk_level="high",
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-write",
                        name="file.write",
                        arguments={"path": "server.asm", "content": "section .text"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=command.command_id,
                    status="needs_user",
                    summary="Denied by policy: operation requires explicit confirmation",
                    error=ActionError(
                        code="CONFIRM_REQUIRED",
                        message="Denied by policy: operation requires explicit confirmation",
                    ),
                ),
            )
        ]
    )
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "waiting_user"
    message = str(result.message or "")
    assert "Policy confirmation required." in message
    assert "file.write" in message
    assert "path=server.asm" in message
    assert "cwd=/tmp/app" in message
    assert "Denied by policy" not in message
    assert ctx.state.pending_confirmation_command is not None
    assert ctx.state.post_action_user_message == message


def test_act_adaptive_truncates_overlong_session_work_summary_mechanically() -> None:
    long_summary = " ".join(f"checkpoint-{i}" for i in range(120))
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=(
                    "Checkpoint captured."
                    "\n<session_work_summary>"
                    + '{"summary": "'
                    + long_summary
                    + '"}'
                    + "</session_work_summary>"
                ),
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The model declares the act-route answer complete.",
                },
                finish_reason="stop",
            )
        ]
    )
    executor = _FakeCommandExecutor(outcomes=[])
    ctx, _services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.action_result is not None
    stored = str(result.action_result.outputs["session_work_summary"] or "")
    assert len(stored) <= 800
    assert not stored.endswith(" ")
    assert "checkpoint-0" in stored


def test_act_adaptive_applies_memory_consolidation_decisions() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Promoted one durable lesson and deferred one noisy candidate.",
                memory_consolidation={
                    "decisions": [
                        {
                            "candidate_id": "cand-1",
                            "action": "promote",
                            "reasoning": "Important durable lesson.",
                        },
                        {
                            "candidate_id": "cand-2",
                            "action": "defer",
                            "reasoning": "Need another confirming turn.",
                        },
                    ]
                },
                finish_reason="stop",
            )
        ]
    )
    executor = _FakeCommandExecutor(outcomes=[])
    services = _FakeServices()
    services.runner = SimpleNamespace(
        tool_api=None,
        memory_api=SimpleNamespace(
            _backend=SimpleNamespace(
                candidate_update=MagicMock(),
                promote_candidate=MagicMock(),
            )
        ),
    )
    ctx, _services = _ctx(llm_client, executor, services=services)
    ctx.state.module_state = {
        "memory_consolidation": {
            "enabled": True,
            "target_scope": "agent:agent",
            "candidates": [
                {
                    "candidate_id": "cand-1",
                    "record_type": "fact",
                    "content_preview": "Useful lesson",
                }
            ],
        }
    }

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.action_result is not None
    assert result.action_result.outputs["memory_consolidation.applied_count"] == 2
    assert result.action_result.outputs["memory_consolidation.promoted_count"] == 1
    assert result.action_result.outputs["memory_consolidation.deferred_count"] == 1
    backend = services.runner.memory_api._backend
    assert backend.promote_candidate.call_count == 1


def test_act_adaptive_forces_answer_only_closure_for_direct_tool_turn() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.list_dir",
                        arguments={"path": "src/openminion"},
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The top-level project folders are src, tests, and docs.",
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed repo root",
                    outputs={"entries": ["src", "tests", "docs"]},
                ),
            ),
        ]
    )
    ctx, services = _ctx(llm_client, executor)
    services.runner = SimpleNamespace(
        tool_api=None,
        _idempotency_key=lambda **_: "idem-direct-tool-clamp",
    )
    ctx.user_input = 'tool file.list_dir {"path":"."}'
    ctx.decision.reason_code = "entry_tool_call"
    ctx.decision._entry_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(id="call-1", name="file.list_dir", arguments={"path": "."})
        ],
    )

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert "top-level project folders" in str(result.message or "").lower()
    assert [call.args["path"] for call in executor.calls] == ["."]
    assert len(llm_client.calls) == 2
    assert llm_client.calls[1]["overrides"]["tool_choice"] == "none"
    assert any(
        "already completed successfully" in str(getattr(message, "content", "") or "")
        for message in llm_client.calls[1]["messages"]
        if getattr(message, "role", "") == "system"
    )


def test_act_adaptive_injects_raw_intent_execution_state_block() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.list_dir",
                        arguments={"path": "src/openminion"},
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The repository contains src, tests, and docs.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Tool-backed repo inspection complete.",
                },
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed repo root",
                    outputs={"entries": ["src", "tests", "docs"]},
                ),
            ),
        ]
    )
    state = _state()
    state.decision_sub_intents = ["inspect repo"]
    state.intent_execution_states = [
        IntentExecutionState(
            intent_id="inspect_repo",
            description="inspect repo",
            status="in_progress",
            depends_on=[],
            last_step_index=0,
            updated_at="2026-04-12T12:00:00Z",
        )
    ]
    ctx, _services = _ctx(llm_client, executor, state=state)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    second_round_messages = llm_client.calls[1]["messages"]
    system_contents = [
        str(getattr(message, "content", "") or "")
        for message in second_round_messages
        if getattr(message, "role", "") == "system"
    ]
    assert any(
        content.startswith("intent_execution_states=") for content in system_contents
    )
    assert any('"intent_id":"inspect_repo"' in content for content in system_contents)
    assert not any("Completed:" in content for content in system_contents)
    assert not any("Reply 'continue'" in content for content in system_contents)


def test_act_adaptive_clamps_overexpanded_entry_batch_for_explicit_tool_command() -> (
    None
):
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.read",
                        arguments={"path": "README.md"},
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The repository root contains src, tests, and docs.",
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed repo root",
                    outputs={"entries": ["src", "tests", "docs"]},
                ),
            ),
        ]
    )
    ctx, services = _ctx(llm_client, executor)
    services.runner = SimpleNamespace(
        tool_api=None,
        _idempotency_key=lambda **_: "idem-direct-tool-clamp",
    )
    ctx.user_input = 'tool file.list_dir {"path":"."}'
    ctx.decision.reason_code = "entry_tool_call"
    ctx.decision._entry_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(id="call-1", name="file.list_dir", arguments={"path": "."}),
            ToolCall(
                id="call-extra", name="file.read", arguments={"path": "README.md"}
            ),
        ],
    )

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert [call.tool_name for call in executor.calls] == ["file.list_dir"]
    assert llm_client.calls[1]["overrides"]["tool_choice"] == "none"


def test_act_adaptive_seeded_confirmation_replay_continue_stays_autonomous() -> None:
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote pyproject",
                    outputs={"path": "demo/pyproject.toml"},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=False,
            next_action="continue",
            reason="Only pyproject.toml exists; keep scaffolding the project.",
        ),
        closure_disposition="continue",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay_validation"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert result.message is None
    assert ctx.state.last_result is not None
    assert ctx.state.last_result.summary == "wrote pyproject"
    assert "Continue the original task: inspect workspace and summarize" in str(
        ctx.state.post_action_user_message
    )


def test_act_adaptive_seeded_confirmation_replay_preserves_autonomous_budget() -> None:
    ctx, _ = _ctx(_FakeLLMClient(), _FakeCommandExecutor())
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="inspect directory",
            tool_name="exec.run",
            args={"command": "ls demo"},
            inputs={"command": "ls demo"},
        )
    ]
    captured: dict[str, Any] = {}

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["profile"] = kwargs.get("profile")
        profile = captured["profile"]
        return AdaptiveToolLoopOutcome(
            profile_name=str(getattr(profile, "profile_name", "") or ""),
            mode_name=str(getattr(profile, "mode_name", "") or ""),
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(getattr(profile, "allowed_tools", frozenset())),
            mode_result=SimpleNamespace(
                status="done",
                working_state=ctx.state,
                message="ok",
            ),
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    profile = captured["profile"]
    assert getattr(profile, "max_iterations", None) == 24
    assert getattr(profile, "max_tool_calls_per_loop", None) == 32


def test_act_adaptive_seeded_non_autonomous_replay_keeps_batch_budget() -> None:
    mode = ActLoopMode()

    assert mode._seeded_replay_loop_limits(
        command_count=1,
        autonomous_recovery=False,
    ) == (1, 1)


def test_act_adaptive_seeded_confirmation_replay_uses_last_user_input_as_goal() -> None:
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="Command exited with code 0.",
                    outputs={},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=False,
            next_action="continue",
            reason="Need to finish the original project request.",
        ),
        closure_disposition="continue",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay_validation"
    ctx.decision.objective = ""
    ctx.state.goal = ""
    ctx.state.last_user_input = "research packaging docs and update pyproject"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="run pytest",
            tool_name="exec.run",
            args={"command": "python -m pytest -q tests"},
            inputs={"command": "python -m pytest -q tests"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert (
        "Continue the original task: research packaging docs and update pyproject"
        in str(ctx.state.post_action_user_message)
    )


def test_act_adaptive_seeded_confirmation_replay_prefers_pending_goal_over_yes() -> (
    None
):
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote files",
                    outputs={},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=False,
            next_action="continue",
            reason="Need to finish the original project request.",
        ),
        closure_disposition="continue",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay_validation"
    ctx.decision.objective = ""
    ctx.state.goal = ""
    ctx.state.last_user_input = "yes"
    ctx.state.pending_confirmation_goal = "update packaging metadata and run tests"
    ctx.state.pending_confirmation_last_user_input = (
        "update pyproject.toml and README.md"
    )
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    guidance = str(ctx.state.post_action_user_message)
    assert (
        "Continue the original task: update packaging metadata and run tests"
        in guidance
    )
    assert "Continue the original task: yes" not in guidance


def test_act_adaptive_seeded_confirmation_replay_preserves_prior_loop_context() -> None:
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="tests passed",
                    outputs={"exit_code": 0},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer="The project update is complete.",
        ),
        closure_disposition="close",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="run tests",
            tool_name="exec.run",
            args={"command": "python -m pytest -q tests"},
            inputs={"command": "python -m pytest -q tests"},
        )
    ]
    ctx.state.module_state = {
        "adaptive_loop": LoopSnapshot(
            turn_scope_id="prior-turn",
            iteration_index=2,
            message_transcript=[
                {"role": "system", "content": "[12 messages compressed]"},
                {
                    "role": "tool",
                    "content": (
                        '{"status": "success", "summary": "dated search results", '
                        '"outputs": {"query_time": "2026-05-25T10:00:00Z", '
                        '"source": "web"}}'
                    ),
                },
                {
                    "role": "tool",
                    "content": (
                        '{"status": "success", "summary": "wrote pyproject", '
                        '"outputs": {"path": "/tmp/demo/pyproject.toml"}}'
                    ),
                },
            ],
            tool_call_history=[
                LoopToolCallRecord(
                    tool_name="web.search",
                    args_hash="",
                    result_summary="dated search results",
                ),
                LoopToolCallRecord(
                    tool_name="file.write",
                    args_hash="",
                    result_summary="wrote pyproject",
                ),
            ],
            budgets_consumed={"llm_calls": 2, "tool_calls": 2},
            profile_name="general_adaptive_v1",
            model="",
            allowed_tools=frozenset(ACT_ADAPTIVE_ALLOWED_TOOLS),
            tool_results=[
                {
                    "tool_name": "web.search",
                    "ok": True,
                    "verified": True,
                    "content": "dated search results",
                    "error": "",
                    "data": {"query_time": "2026-05-25T10:00:00Z", "source": "web"},
                    "error_code": "",
                    "call_id": "call-search",
                    "source": "native",
                },
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "verified": True,
                    "content": "wrote pyproject",
                    "error": "",
                    "data": {"path": "/tmp/demo/pyproject.toml"},
                    "error_code": "",
                    "call_id": "call-write",
                    "source": "native",
                },
            ],
        ).to_dict()
    }

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert ctx.state.last_result is not None
    outputs = dict(ctx.state.last_result.outputs or {})
    assert outputs["tool_execution_count"] == 3
    assert outputs["adaptive.tool_calls_total"] == 1
    assert [item["tool_name"] for item in outputs["tool_results"]] == [
        "web.search",
        "file.write",
        "exec.run",
    ]
    assert "adaptive_loop" not in dict(ctx.state.module_state or {})


def test_act_adaptive_seeded_confirmation_replay_does_not_reinject_goal_as_user_turn() -> (
    None
):
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)
    ctx.user_input = None
    ctx.state.goal = "create the scratch project from the original request"
    ctx.state.post_action_user_message = (
        "Continue from the current task state. "
        "Only give a final answer after the task is actually satisfied."
    )
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]
    captured: dict[str, Any] = {}

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["initial_messages"] = list(kwargs.get("initial_messages") or [])
        return AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_loop_adaptive",
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(),
            mode_result=SimpleNamespace(
                status="done",
                working_state=ctx.state,
                message="ok",
            ),
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert captured["initial_messages"] == []


def test_act_adaptive_confirmation_replay_without_seeded_batch_does_not_reinject_goal() -> (
    None
):
    llm_client = _FakeLLMClient()
    executor = _FakeCommandExecutor()
    ctx, _services = _ctx(llm_client, executor)
    ctx.user_input = None
    ctx.state.goal = "resume the original scratch-project task"
    ctx.decision.reason_code = "confirmation_replay"
    captured: dict[str, Any] = {}

    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        captured["initial_messages"] = list(kwargs.get("initial_messages") or [])
        return AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name="act_loop_adaptive",
            termination_reason="final_text",
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset(),
            mode_result=SimpleNamespace(
                status="done",
                working_state=ctx.state,
                message="ok",
            ),
        )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert captured["initial_messages"] == []


def test_act_adaptive_seeded_confirmation_replay_reopens_missing_plan_wait_state() -> (
    None
):
    class _MissingPlanAdvanceExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            advance_after_action(
                SimpleNamespace(
                    options=SimpleNamespace(
                        plan_consecutive_failure_limit=3,
                        max_retries_per_step=1,
                        max_replans=0,
                        adaptive_replan_retained_step_outputs=0,
                    )
                ),
                state=state,
                action_result=action_result,
                force_replan=force_replan,
                logger=logger,
            )

    executor = _MissingPlanAdvanceExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote pyproject",
                    outputs={"path": "demo/pyproject.toml"},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=False,
            next_action="continue",
            reason="Only pyproject.toml exists; keep scaffolding the project.",
        ),
        closure_disposition="continue",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert result.message is None
    assert ctx.state.last_result is not None
    assert ctx.state.last_result.summary == "wrote pyproject"
    follow_up = str(ctx.state.post_action_user_message or "")
    assert "Continue from the current task state." in follow_up
    assert "Only give a final answer after the task is actually satisfied." in (
        follow_up
    )


def test_act_adaptive_seeded_confirmation_replay_close_disposition_reopens_autonomous() -> (
    None
):
    class _AdvanceWithStateExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            advance_after_action(
                SimpleNamespace(
                    options=SimpleNamespace(
                        plan_consecutive_failure_limit=3,
                        max_retries_per_step=1,
                        max_replans=0,
                        adaptive_replan_retained_step_outputs=0,
                    )
                ),
                state=state,
                action_result=action_result,
                force_replan=force_replan,
                logger=logger,
            )

    executor = _AdvanceWithStateExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote pyproject",
                    outputs={"path": "demo/pyproject.toml"},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer="done",
        ),
        closure_disposition="close",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert result.message is None
    assert ctx.state.last_result is not None
    assert ctx.state.last_result.summary == "wrote pyproject"


def test_act_adaptive_seeded_confirmation_replay_verification_close_uses_final_answer() -> (
    None
):
    class _AdvanceWithStateExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            advance_after_action(
                SimpleNamespace(
                    options=SimpleNamespace(
                        plan_consecutive_failure_limit=3,
                        max_retries_per_step=1,
                        max_replans=0,
                        adaptive_replan_retained_step_outputs=0,
                    )
                ),
                state=state,
                action_result=action_result,
                force_replan=force_replan,
                logger=logger,
            )

    command = ToolCommand(
        title="run pytest",
        tool_name="exec.run",
        args={"command": "python -m pytest -q tests"},
        inputs={
            "command": "python -m pytest -q tests",
            "confirmation_source": "policy_replay",
        },
    )
    executor = _AdvanceWithStateExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="tests passed",
                    outputs={"exit_code": 0},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer=(
                "SOURCES\n- PyPA\n\nCHANGES\n- Added project script\n\n"
                "TESTS\n- python -m pytest -q tests passed"
            ),
        ),
        closure_disposition="close",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [command]
    ctx.state.plan = Plan(
        objective="research packaging docs and update project",
        steps=[command],
        stop_conditions=[],
        assumptions=[],
        risk_summary="confirmation_replay",
    )

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.message.startswith("SOURCES")
    assert result.action_result is not None
    assert str(result.action_result.summary).startswith("tests passed")


def test_act_adaptive_seeded_entry_tool_close_disposition_reopens_autonomous() -> None:
    class _AdvanceWithStateExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            advance_after_action(
                SimpleNamespace(),
                state=state,
                action_result=action_result,
                force_replan=force_replan,
                logger=logger,
            )

    executor = _AdvanceWithStateExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote report",
                    outputs={"path": "demo/task_summary/report.py"},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer="Let me run pytest now.",
        ),
        closure_disposition="close",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "entry_tool_call"
    ctx.state.last_user_input = "Update the package, run pytest, then return SOURCES."
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write report",
            tool_name="file.write",
            args={"path": "demo/task_summary/report.py", "body": "print('ok')"},
            inputs={"path": "demo/task_summary/report.py", "body": "print('ok')"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert result.message is None
    assert "Continue the original task: inspect workspace and summarize" in str(
        ctx.state.post_action_user_message or ""
    )


def test_act_adaptive_seeded_entry_mutation_batch_continues_without_closure_judge() -> (
    None
):
    class _AdvanceWithStateExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            advance_after_action(
                SimpleNamespace(),
                state=state,
                action_result=action_result,
                force_replan=force_replan,
                logger=logger,
            )

    class _ClosureShouldNotRunServices(_FakeServices):
        def evaluate_turn_closure(self, **kwargs) -> ClosureJudgment:
            raise AssertionError(
                "closure judge should not run for obvious partial work"
            )

    executor = _AdvanceWithStateExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote pyproject",
                    outputs={"path": "demo/pyproject.toml"},
                ),
            ),
        ]
    )
    services = _ClosureShouldNotRunServices()
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "entry_tool_call"
    ctx.state.last_user_input = (
        "Update pyproject.toml and README.md, then run pytest and return "
        "SOURCES, CHANGES, TESTS."
    )
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert "Continue the original task" in str(ctx.state.post_action_user_message or "")


def test_act_adaptive_unexecutable_detector_rejects_tool_transcript_prose() -> None:
    assert not ActLoopMode()._seeded_final_text_is_unexecutable_tool_envelope(
        'Tool used: file.read\nPath: /tmp/demo/pyproject.toml\n{"status": "ok"}'
    )


def test_act_adaptive_unexecutable_detector_rejects_file_write_args() -> None:
    assert ActLoopMode()._seeded_final_text_is_unexecutable_tool_envelope(
        '{"path": "/tmp/demo/pyproject.toml", "content": "[project]\\nname = \\"x\\""}'
    )


def test_act_adaptive_seeded_entry_tool_unexecutable_final_text_reopens_autonomous() -> (
    None
):
    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        finalizer = kwargs["finalizer"]
        mode_result = finalizer(
            AdaptiveToolLoopOutcome(
                profile_name="general_adaptive_v1",
                mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
                termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
                state=AdaptiveToolLoopState(),
                allowed_tools=frozenset({"file.read", "file.write", "web.search"}),
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "The model attempted to finalize with tool-shaped text.",
                },
                final_text=(
                    "I can see the duplicate pyproject sections. Let me run "
                    "verification now.\n```json\n"
                    '{"tool": "exec.run", "arguments": {"command": '
                    '"python -m pytest -q tests"}}\n```'
                ),
            )
        )
        return AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset({"file.read", "file.write", "web.search"}),
            mode_result=mode_result,
        )

    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer="Looks done.",
        ),
        closure_disposition="close",
    )
    ctx, _ = _ctx(_FakeLLMClient(), _FakeCommandExecutor(), services=services)
    ctx.decision.reason_code = "entry_tool_call"
    ctx.state.status = BRAIN_STATE_WAITING_USER
    ctx.state.last_user_input = (
        "Research sources, update the package, and return SOURCES."
    )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert result.message is None
    assert "raw or unexecutable tool markup" in str(
        ctx.state.post_action_user_message or ""
    )
    assert "call the next required native tool now" in str(
        ctx.state.post_action_user_message or ""
    )
    assert "Continue the original task: Research sources" in str(
        ctx.state.post_action_user_message or ""
    )
    assert ctx.state.module_state["seeded_final_text_autonomous_retry"] == {"count": 1}


def test_act_adaptive_uses_closure_gate_final_answer_when_closing() -> None:
    def _fake_run_adaptive_tool_loop(*args, **kwargs):
        del args
        finalizer = kwargs["finalizer"]
        mode_result = finalizer(
            AdaptiveToolLoopOutcome(
                profile_name="general_adaptive_v1",
                mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
                termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
                state=AdaptiveToolLoopState(),
                allowed_tools=frozenset({"file.read", "file.write", "exec.run"}),
                final_text=(
                    "Based on the available workspace context, the project appears "
                    "to be set up."
                ),
            )
        )
        return AdaptiveToolLoopOutcome(
            profile_name="general_adaptive_v1",
            mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=AdaptiveToolLoopState(),
            allowed_tools=frozenset({"file.read", "file.write", "exec.run"}),
            mode_result=mode_result,
        )

    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer="SOURCES\n- PyPA\n\nCHANGES\n- Updated pyproject\n\nTESTS\n- pytest passed",
        ),
        closure_disposition="close",
    )
    ctx, _ = _ctx(_FakeLLMClient(), _FakeCommandExecutor(), services=services)
    ctx.decision.reason_code = "entry_tool_call"
    ctx.state.last_user_input = (
        "Research sources, update the package, and return SOURCES, CHANGES, TESTS."
    )

    with patch(
        "openminion.modules.brain.loop.adaptive.run_adaptive_tool_loop",
        side_effect=_fake_run_adaptive_tool_loop,
    ):
        result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert result.message.startswith("SOURCES")
    assert result.action_result is not None
    assert str(result.action_result.summary).startswith("SOURCES")


def test_act_adaptive_iteration_cap_can_close_with_closure_gate_final_answer() -> None:
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=True,
            next_action="close",
            final_answer=(
                "SOURCES\n- https://packaging.python.org/en/latest/guides/"
                "writing-pyproject-toml/\n\nCHANGES\n- Added project script\n\n"
                "TESTS\n- python -m pytest -q tests passed"
            ),
        ),
        closure_disposition="close",
    )
    ctx, _ = _ctx(_FakeLLMClient(), _FakeCommandExecutor(), services=services)
    ctx.state.last_user_input = (
        "Research PyPA scripts, update pyproject, run pytest, return SOURCES."
    )
    outcome = AdaptiveToolLoopOutcome(
        profile_name="general_adaptive_v1",
        mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
        termination_reason=ADAPTIVE_TERM_ITERATION_CAP,
        state=AdaptiveToolLoopState(
            scratchpad={
                "adaptive.tool_results": [
                    {
                        "tool_name": "file.write",
                        "ok": True,
                        "content": "wrote pyproject",
                    },
                    {
                        "tool_name": "exec.run",
                        "ok": True,
                        "content": "1 passed",
                    },
                ]
            }
        ),
        allowed_tools=frozenset({"file.read", "file.write", "exec.run"}),
    )

    result = ActLoopMode()._result_from_outcome(ctx, outcome=outcome)

    assert result.status == "done"
    assert result.message.startswith("SOURCES")
    assert result.action_result is not None
    assert result.action_result.status == "success"
    assert result.action_result.error is None


def test_act_adaptive_seeded_confirmation_replay_close_disposition_reopens_from_waiting_user() -> (
    None
):
    ctx, _ = _ctx(_FakeLLMClient(), _FakeCommandExecutor())
    ctx.state.status = BRAIN_STATE_WAITING_USER

    result = ActLoopMode()._autonomous_seeded_result(
        ctx,
        action_result=ActionResult(
            command_id=new_uuid(),
            status="success",
            summary="wrote pyproject",
            outputs={"path": "demo/pyproject.toml"},
        ),
    )

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert result.message is None


def test_act_adaptive_seeded_confirmation_replay_policy_denial_reopens_autonomous() -> (
    None
):
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="Denied by policy: command 'cd' is not allowlisted",
                    error=ActionError(
                        code="POLICY_DENIED",
                        message="Denied by policy: command 'cd' is not allowlisted",
                        details={
                            "tool_name": "exec.run",
                            "suggested_tool": "exec.run",
                            "suggested_fix": (
                                "Pass the directory with the exec.run workdir/cwd "
                                "argument instead of `cd ... &&`."
                            ),
                        },
                    ),
                ),
            ),
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Recovered with structured tool guidance.",
                finish_reason="stop",
            )
        ]
    )
    ctx, _ = _ctx(llm_client, executor)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="run pytest",
            tool_name="exec.run",
            args={"command": "cd demo && python -m pytest -q tests"},
            inputs={"command": "cd demo && python -m pytest -q tests"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert ctx.state.last_result is not None
    assert ctx.state.last_result.summary == "[act] completed."
    assert llm_client.calls
    assert any(
        "Retry the same user task using exec.run" in message.content
        for message in llm_client.calls[0]["messages"]
    )


def test_act_adaptive_seeded_confirmation_replay_blocked_policy_denial_reopens_autonomous() -> (
    None
):
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="blocked",
                    summary="Denied by policy: command 'cd' is not allowlisted",
                    error=ActionError(
                        code="POLICY_DENIED",
                        message="Denied by policy: command 'cd' is not allowlisted",
                        details={
                            "tool_name": "exec.run",
                            "suggested_tool": "exec.run",
                            "suggested_fix": (
                                "Pass the directory with the exec.run workdir/cwd "
                                "argument instead of `cd ... &&`."
                            ),
                        },
                    ),
                ),
            ),
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Recovered with structured tool guidance.",
                finish_reason="stop",
            )
        ]
    )
    ctx, _ = _ctx(llm_client, executor)
    ctx.decision.reason_code = "confirmation_replay_validation"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="run pytest",
            tool_name="exec.run",
            args={"command": "cd demo && python -m pytest -q tests"},
            inputs={"command": "cd demo && python -m pytest -q tests"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert ctx.state.last_result is not None
    assert ctx.state.last_result.summary == "[act] completed."
    assert llm_client.calls
    assert any(
        "Retry the same user task using exec.run" in message.content
        for message in llm_client.calls[0]["messages"]
    )


def test_act_adaptive_seeded_confirmation_replay_lost_reason_policy_denial_uses_hint() -> (
    None
):
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="blocked",
                    summary="Denied by policy: command 'find' is not allowlisted",
                    outputs={
                        "error": {
                            "code": "POLICY_DENIED",
                            "message": (
                                "Denied by policy: command 'find' is not allowlisted"
                            ),
                            "details": {
                                "tool_name": "exec.run",
                                "suggested_tool": "file.find",
                                "suggested_fix": (
                                    "Use file.find or file.list_dir(recursive=True) "
                                    "instead of shelling out to find."
                                ),
                            },
                        },
                    },
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="found python files",
                    outputs={"matches": ["demo/app.py"]},
                ),
            ),
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="find-1",
                        name="file.find",
                        arguments={"path": "demo", "pattern": "*.py"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Recovered with file.find guidance.",
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Recovered with file.find guidance.",
                finish_reason="stop",
            ),
        ]
    )
    ctx, _ = _ctx(llm_client, executor)
    ctx.decision.reason_code = ""
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="find python files",
            tool_name="exec.run",
            args={"command": "find demo -type f -name '*.py' | head -20"},
            inputs={"command": "find demo -type f -name '*.py' | head -20"},
        ),
        ToolCommand(
            title="stale sibling command",
            tool_name="exec.run",
            args={"command": "ls demo"},
            inputs={"command": "ls demo"},
        ),
    ]

    result = ActLoopMode().execute(ctx)

    # The invariant under test is that a lost reason_code still recovers to the
    # suggested structured tool instead of surfacing the seeded policy denial to
    # the user. The final closeout shape is owned by the loop finalizer.
    assert result.status in {"active", "done"}
    assert len(executor.calls) == 2
    assert getattr(executor.calls[1], "tool_name", "") == "file.find"
    assert llm_client.calls
    assert any(
        "Retry the same user task using file.find" in message.content
        for message in llm_client.calls[0]["messages"]
    )


def test_act_adaptive_seeded_confirmation_replay_exec_failure_reopens_autonomous() -> (
    None
):
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="failed",
                    summary="Command exited with code 1.",
                    outputs={
                        "error": {
                            "code": "EXEC_ERROR",
                            "message": "command exited with code 1",
                        },
                        "stderr_preview": "Usage: task-summary <input.csv> <output.md>",
                    },
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="pytest passed",
                    outputs={"stdout_preview": "1 passed"},
                ),
            ),
        ]
    )
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="pytest-1",
                        name="exec.run",
                        arguments={"command": "python -m pytest -q tests"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Recovered after failed command.",
                finish_reason="stop",
            ),
        ]
    )
    ctx, _ = _ctx(llm_client, executor)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="run console script",
            tool_name="exec.run",
            args={"command": "python -m task_summary.report"},
            inputs={"command": "python -m task_summary.report"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert len(executor.calls) == 2
    assert getattr(executor.calls[1], "tool_name", "") == "exec.run"
    assert llm_client.calls
    assert any(
        "confirmed seeded tool command failed" in message.content
        and "Usage: task-summary" in message.content
        for message in llm_client.calls[0]["messages"]
    )


def test_act_adaptive_seeded_confirmation_replay_ask_user_stays_autonomous() -> None:
    class _AdvanceAwareExecutor(_FakeCommandExecutor):
        def advance_after_action(
            self,
            *,
            state,
            action_result,
            force_replan: bool = False,
            logger=None,
        ) -> None:
            del state, action_result, force_replan, logger

    executor = _AdvanceAwareExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="wrote pyproject",
                    outputs={"path": "demo/pyproject.toml"},
                ),
            ),
        ]
    )
    services = _FakeServices(
        closure_judgment=ClosureJudgment(
            satisfied=False,
            next_action="continue",
            reason="The scratch project still needs the remaining files.",
        ),
        closure_disposition="ask_user",
    )
    ctx, _ = _ctx(_FakeLLMClient(), executor, services=services)
    ctx.decision.reason_code = "confirmation_replay"
    ctx.decision._seeded_commands = [
        ToolCommand(
            title="write pyproject",
            tool_name="file.write",
            args={"path": "demo/pyproject.toml", "body": "[project]"},
            inputs={"path": "demo/pyproject.toml", "body": "[project]"},
        )
    ]

    result = ActLoopMode().execute(ctx)

    assert result.status == "active"
    assert result.working_state.status == "active"
    assert result.message is None
    assert ctx.state.last_result is not None
    assert ctx.state.last_result.summary == "wrote pyproject"
    assert "Continue from the current task state" in str(
        ctx.state.post_action_user_message or ""
    )
    assert "Recent progress created or updated files" in str(
        ctx.state.post_action_user_message or ""
    )


def test_act_adaptive_forces_answer_only_closure_after_successful_duplicate_batch() -> (
    None
):
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-2",
                        name="file.list_dir",
                        arguments={"path": "."},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="The repository root contains src, tests, and docs.",
                finish_reason="stop",
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="listed repo root",
                    outputs={"entries": ["src", "tests", "docs"]},
                ),
            ),
        ]
    )
    ctx, services = _ctx(llm_client, executor)
    services.runner = SimpleNamespace(
        tool_api=None,
        _idempotency_key=lambda **_: "idem-duplicate-batch-closure",
    )
    ctx.user_input = "inspect the repo root"
    ctx.decision._entry_response = LLMResponse(
        ok=True,
        provider="fake",
        model="fake-model",
        output_text="",
        tool_calls=[
            ToolCall(id="call-1", name="file.list_dir", arguments={"path": "."})
        ],
    )

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert "repository root contains src" in str(result.message or "").lower()
    assert [call.args["path"] for call in executor.calls] == ["."]
    assert len(llm_client.calls) == 2
    assert llm_client.calls[1]["overrides"]["tool_choice"] == "none"
    assert any(
        "already completed successfully" in str(getattr(message, "content", "") or "")
        for message in llm_client.calls[1]["messages"]
        if getattr(message, "role", "") == "system"
    )


def test_act_adaptive_stops_cleanly_on_needs_user() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1", name="exec.run", arguments={"cmd": "rm -rf build"}
                    )
                ],
            )
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="needs_user",
                    summary="Please approve deleting build artifacts.",
                ),
            )
        ]
    )
    ctx, services = _ctx(llm_client, executor)

    result = ActLoopMode().execute(ctx)

    assert result.status == "waiting_user"
    assert "approve" in str(result.message or "").lower()
    assert any(
        (item.get("payload") or {}).get("adaptive.termination_reason") == "needs_user"
        for item in services.statuses
    )


def test_act_adaptive_parallelizes_independent_reads_and_emits_loop_telemetry() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="file.read",
                        arguments={"path": "/src/alpha.py"},
                    ),
                    ToolCall(
                        id="call-2",
                        name="file.read",
                        arguments={"path": "/src/beta.py"},
                    ),
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Both file reads were summarized.",
                },
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        delays_by_path={"/src/alpha.py": 0.2, "/src/beta.py": 0.2},
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="alpha",
                ),
            ),
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="beta",
                ),
            ),
        ],
    )
    ctx, services = _ctx(llm_client, executor)

    started = time.monotonic()
    result = ActLoopMode().execute(ctx)
    elapsed = time.monotonic() - started

    assert result.status == "done"
    assert elapsed < 0.35
    payload = result.action_result.outputs if result.action_result else {}
    assert payload["loop.parallel_fan_out_count"] == 1
    assert payload["loop.tool_calls_parallel"] == 2
    assert payload["loop.tool_calls_sequential"] == 0
    status_payloads = [item.get("payload") or {} for item in services.statuses]
    assert any(
        item.get("loop.parallel_fan_out_count") == 1
        and item.get("loop.tool_calls_parallel") == 2
        and item.get("loop.tool_calls_sequential") == 0
        for item in status_payloads
    )


def test_act_adaptive_ignores_stale_snapshot_from_previous_trace() -> None:
    llm_client = _FakeLLMClient(
        responses=[
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="weather",
                        arguments={"location": "San Francisco"},
                    )
                ],
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Sunny in San Francisco.",
                finalization_status={
                    "status": "final_answer",
                    "reasoning": "Weather tool result summarized.",
                },
            ),
        ]
    )
    executor = _FakeCommandExecutor(
        outcomes=[
            CommandExecutionOutcome(
                approved_command=SimpleNamespace(),
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="72F and sunny",
                    outputs={"forecast": "sunny"},
                ),
            ),
        ]
    )
    state = _state()
    state.trace_id = "trace-weather-new"
    state.module_state = {
        "adaptive_loop": LoopSnapshot(
            turn_scope_id="trace-weather-old",
            iteration_index=7,
            message_transcript=[],
            tool_call_history=[],
            budgets_consumed={"llm_calls": 4, "tool_calls": 4},
            profile_name="general_adaptive_v1",
            model="",
            allowed_tools=ACT_ADAPTIVE_ALLOWED_TOOLS,
        ).to_dict()
    }
    ctx, services = _ctx(llm_client, executor, state=state)

    result = ActLoopMode().execute(ctx)

    assert result.status == "done"
    assert [call.tool_name for call in executor.calls] == ["weather"]
    assert any(
        (item.get("payload") or {}).get("adaptive.tool_calls_total") == 1
        for item in services.statuses
    )
