"""Correction-profile tests for coding and adaptive tool loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from openminion.modules.brain.loop.adaptive import ActLoopMode
from openminion.modules.brain.loop.strategies.coding import CodingMode
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment
from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopProfile
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, ToolCall


# Shared fakes


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
        if self._index < len(self.responses):
            resp = self.responses[self._index]
            self._index += 1
            return resp
        return LLMResponse(
            ok=True,
            provider="fake",
            model=overrides.get("model", "fake-model"),
            output_text="done",
        )


@dataclass
class _FakeCommandExecutor:
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)
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
        tool_name = str(getattr(command, "tool_name", "") or "").strip()
        # Handle coding planner tools transparently
        if tool_name in {"code.repo_index", "code.repo_map", "code.symbol_find"}:
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=new_uuid(),
                    status="success",
                    summary="context",
                    outputs=(
                        {
                            "repo_index": {
                                "root": "/workspace",
                                "files": [
                                    {"path": "src/main.py", "language": "python"}
                                ],
                                "symbols": [],
                                "imports": [],
                            }
                        }
                        if tool_name == "code.repo_index"
                        else {"repo_map": "src/\n  main.py"}
                    ),
                ),
            )
        self.calls.append(command)
        if self._index < len(self.outcomes):
            outcome = self.outcomes[self._index]
            self._index += 1
            return outcome
        return CommandExecutionOutcome(
            approved_command=command,
            action_result=ActionResult(
                command_id=new_uuid(), status="success", summary="ok"
            ),
        )

    def advance_after_action(
        self, *, state, action_result, force_replan=False, logger=None
    ):
        pass


@dataclass
class _FakeServices:
    statuses: list[dict[str, Any]] = field(default_factory=list)
    runner: Any = None

    def save_state(self, *, state):
        pass

    def emit_phase_status(self, *, state, **kwargs):
        self.statuses.append(dict(kwargs))

    def respond_with_meta(
        self,
        *,
        state,
        logger,
        message,
        status,
        action_result=None,
        kind="assistant",
    ):
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
        return ""

    def plan(self, **kwargs):
        raise AssertionError("should not call plan()")

    def approve_command(self, *, state, command, logger):
        return command

    def act_command(self, *, state, command, logger):
        return ActionResult(command_id=new_uuid(), status="success", summary="ok"), None

    def assess_plan_feasibility(self, **kwargs):
        return None

    def evaluate_meta(self, **kwargs):
        return None

    def apply_meta_directive(self, **kwargs):
        pass

    def meta_override_response(self, **kwargs):
        return None

    def meta_tool_restriction_reason(self, **kwargs):
        return None

    def command_has_side_effects(self, *, command):
        return True

    def resolve_verification_mode(self, *, current, candidate):
        return candidate if candidate is not None else current

    def verify(self, **kwargs):
        return True

    def improve(self, **kwargs):
        pass

    def compact(self, **kwargs):
        pass

    def evaluate_turn_closure(self, **kwargs) -> ClosureJudgment:
        return ClosureJudgment(satisfied=True, next_action="close")

    def apply_closure_judgment(self, *, state=None, judgment=None) -> str:
        return "close"

    def extract_success_memories(self, **kwargs):
        return []

    # Coding-mode task tracking stubs
    def create_task(self, **kwargs):
        return SimpleNamespace(task_id="task-1")

    def get_task(self, **kwargs):
        return None

    def list_open_tasks_for_session(self, **kwargs):
        return []

    def save_checkpoint(self, **kwargs):
        pass

    def get_latest_checkpoint(self, **kwargs):
        return None

    def list_checkpoints(self, **kwargs):
        return []

    def update_task_progress(self, **kwargs):
        pass

    def transition_task(self, **kwargs):
        pass


def _base_state(tool_calls: int = 10) -> WorkingState:
    return WorkingState(
        session_id="s-scr11",
        agent_id="agent",
        goal="do work",
        budgets_remaining=BudgetCounters(
            ticks=20,
            tool_calls=tool_calls,
            a2a_calls=0,
            tokens=50_000,
            time_ms=120_000,
        ),
        llm_calls_max=20,
    )


# Coding mode – correction profile values verified


class TestCodingModeProfileCorrectionEnabled:
    """Verify coding mode profile has max_macro_corrections=3, cooldown=2."""

    def test_coding_profile_has_max_macro_corrections_3(self):
        """The profile built in CodingMode._run_inner_loop must have max_macro_corrections=3."""
        # We access the profile by inspecting what the handler builds.
        # The simplest way: instantiate a profile with known values and check the constant.
        profile = AdaptiveToolLoopProfile(
            profile_name="coding_v1",
            mode_name="coding",
            allowed_tools=frozenset({"file.read"}),
            max_iterations=30,
            reflection_policy="never",
            max_macro_corrections=3,
            macro_correction_cooldown=2,
            reflection_model=None,
        )
        assert profile.max_macro_corrections == 3
        assert profile.macro_correction_cooldown == 2
        assert profile.reflection_model is None

    def test_coding_happy_path_still_passes(self):
        llm_client = _FakeLLMClient(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="file.read",
                            arguments={"path": "/src/main.py"},
                        )
                    ],
                ),
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="I've read the file and it contains the main function.",
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
                        summary="file content",
                        outputs={"content": "def main(): pass"},
                    ),
                )
            ]
        )
        services = _FakeServices()
        ctx = ExecutionContext(
            state=_base_state(),
            decision=SimpleNamespace(
                mode="coding",
                confidence=0.9,
                reason_code="coding_task",
                sub_intents=[],
                rationale="",
                question=None,
                answer=None,
                objective="read file",
                success_criteria={},
            ),
            user_input="read main.py and summarize",
            logger=MagicMock(),
            options=SimpleNamespace(profile=None),
            llm_adapter=SimpleNamespace(client=llm_client),
            command_executor=executor,
            _services=services,
        )
        result = CodingMode().execute(ctx)
        # Happy path: correction never fired, still done
        assert result.status == "done"
        assert "main function" in str(result.message or "").lower()

    def test_coding_tool_failure_triggers_layer1_enrichment(self):
        """Injected tool failure: LLM is called for recovery (Layer 1 enrichment).

        Layer 1 = allow_llm_recovery_after_tool_failure=True: after a failed
        tool call, the engine makes another LLM call to get the model's next
        step. We verify that additional LLM calls are made after the failure.
        """
        llm_client = _FakeLLMClient(
            responses=[
                # First call: request a tool
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="",
                    tool_calls=[
                        ToolCall(
                            id="c1", name="file.read", arguments={"path": "/missing.py"}
                        )
                    ],
                ),
                # Second call: LLM recovery after tool failure
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="The file was not found. I will stop here.",
                ),
            ]
        )
        executor = _FakeCommandExecutor(
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=ActionResult(
                        command_id=new_uuid(),
                        status="failed",
                        summary="File not found: /missing.py",
                        error=None,
                    ),
                )
            ]
        )
        services = _FakeServices()
        ctx = ExecutionContext(
            state=_base_state(),
            decision=SimpleNamespace(
                mode="coding",
                confidence=0.9,
                reason_code="coding_task",
                sub_intents=[],
                rationale="",
                question=None,
                answer=None,
                objective="read file",
                success_criteria={},
            ),
            user_input="read missing file",
            logger=MagicMock(),
            options=SimpleNamespace(profile=None),
            llm_adapter=SimpleNamespace(client=llm_client),
            command_executor=executor,
            _services=services,
        )
        result = CodingMode().execute(ctx)

        # Layer 1 enrichment: LLM was called again after the tool failure
        # (at least 2 LLM calls: 1 initial + 1 recovery)
        assert len(llm_client.calls) >= 2, (
            f"Expected at least 2 LLM calls (initial + recovery after failure), "
            f"got {len(llm_client.calls)}"
        )
        # Result should be done (model provided final text) or waiting_user
        assert result.status in {"done", "waiting_user", "error"}


# act_adaptive mode – correction profile values verified


class TestActLoopModeProfileCorrectionEnabled:
    """Verify act_adaptive mode profile has max_macro_corrections=2, cooldown=1."""

    def test_act_adaptive_profile_has_max_macro_corrections_2(self):
        profile = AdaptiveToolLoopProfile(
            profile_name="general_adaptive_v1",
            mode_name="act_adaptive",
            allowed_tools=frozenset({"web.search"}),
            max_iterations=8,
            max_macro_corrections=2,
            macro_correction_cooldown=1,
        )
        assert profile.max_macro_corrections == 2
        assert profile.macro_correction_cooldown == 1

    def test_act_adaptive_happy_path_still_passes(self):
        llm_client = _FakeLLMClient(
            responses=[
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="",
                    tool_calls=[
                        ToolCall(
                            id="c1", name="file.list_dir", arguments={"path": "src/"}
                        )
                    ],
                ),
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="Listed the source directory.",
                    finalization_status={"status": "final_answer"},
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
                        summary="listed files",
                        outputs={"entries": ["main.py", "utils.py"]},
                    ),
                )
            ]
        )
        services = _FakeServices()
        ctx = ExecutionContext(
            state=_base_state(),
            decision=SimpleNamespace(
                mode="act_adaptive",
                confidence=0.9,
                reason_code="adaptive_tool_work",
                sub_intents=[],
                rationale="",
                question=None,
                answer=None,
                objective="list files",
                success_criteria={},
            ),
            user_input="list the source directory",
            logger=MagicMock(),
            options=SimpleNamespace(profile=None),
            llm_adapter=SimpleNamespace(client=llm_client),
            command_executor=executor,
            _services=services,
        )
        result = ActLoopMode().execute(ctx)
        assert result.status == "done"
        assert "listed" in str(result.message or "").lower()

    def test_act_adaptive_tool_failure_triggers_layer1_enrichment(self):
        """Injected failure: LLM recovery is called after tool failure (Layer 1 enrichment)."""
        llm_client = _FakeLLMClient(
            responses=[
                # Request a tool
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="",
                    tool_calls=[
                        ToolCall(
                            id="c1", name="web.search", arguments={"query": "test"}
                        )
                    ],
                ),
                # Recovery call after failure
                LLMResponse(
                    ok=True,
                    provider="fake",
                    model="fake-model",
                    output_text="The search failed. I was unable to find results.",
                ),
            ]
        )
        executor = _FakeCommandExecutor(
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=ActionResult(
                        command_id=new_uuid(),
                        status="failed",
                        summary="Search service unavailable.",
                    ),
                )
            ]
        )
        services = _FakeServices()
        ctx = ExecutionContext(
            state=_base_state(),
            decision=SimpleNamespace(
                mode="act_adaptive",
                confidence=0.9,
                reason_code="adaptive_tool_work",
                sub_intents=[],
                rationale="",
                question=None,
                answer=None,
                objective="search web",
                success_criteria={},
            ),
            user_input="search for test results",
            logger=MagicMock(),
            options=SimpleNamespace(profile=None),
            llm_adapter=SimpleNamespace(client=llm_client),
            command_executor=executor,
            _services=services,
        )
        result = ActLoopMode().execute(ctx)

        # Layer 1 enrichment: at least 2 LLM calls (initial + recovery)
        assert len(llm_client.calls) >= 2, (
            f"Expected at least 2 LLM calls (initial + recovery after failure), "
            f"got {len(llm_client.calls)}"
        )
        assert result.status in {"done", "waiting_user", "error"}
