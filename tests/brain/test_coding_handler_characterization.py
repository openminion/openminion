from __future__ import annotations

import json
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_SUCCESS
from openminion.modules.brain.loop.strategies.coding import handler
from openminion.modules.brain.loop.strategies.coding import runtime as coding_runtime
from openminion.modules.brain.loop.strategies.coding.handler import (
    CodingMode,
    CodingProfileRunner,
    execute_coding_profile,
    prepare_coding_profile,
)
from openminion.modules.brain.loop.strategies.coding.plan import CodingPlan
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.loop.strategies.coding.contracts import (
    CODING_TERM_BUDGET_EXHAUSTED,
    CODING_TERM_DISALLOWED_TOOL,
    CODING_TERM_FINAL_TEXT,
    CODING_TERM_TOOL_FAILURE,
    CODING_TERM_VERIFY_CAP_EXCEEDED,
)
from openminion.modules.brain.schemas import ActionResult, BudgetCounters, ToolCommand
from openminion.modules.llm.schemas import Message


# Public symbols downstream imports rely on. Anything in this list MUST
# remain importable from `...coding.handler` after the split.
EXPECTED_HANDLER_SYMBOLS: tuple[str, ...] = (
    # Public entry points (also re-exported from the package __init__).
    "execute_coding_profile",
    "prepare_coding_profile",
    # Public classes.
    "CodingProfileRunner",
    "CodingMode",
    # Module-private helpers consumed by other modules in the package.
    # These are file-internal today; if the split moves them to a sibling
    # file they must still be importable from `handler` (shim re-export).
    "_CodingLoopContextAdapter",
    "_runner_and_profile_from_context",
    "_coding_mode_config_from_context",
    "_configured_coding_profile_runner",
    "_build_error_result",
    "_build_blocked_result",
    "_resolve_model",
    "_build_tool_specs",
    "_is_budget_exhausted",
)


class TestCodingHandlerSurface:
    @pytest.mark.parametrize("name", EXPECTED_HANDLER_SYMBOLS)
    def test_every_expected_symbol_resolves(self, name: str) -> None:
        assert hasattr(handler, name), f"handler.py lost symbol `{name}`."

    def test_coding_mode_inherits_from_coding_profile_runner(self) -> None:
        assert issubclass(CodingMode, CodingProfileRunner)

    def test_coding_profile_runner_is_a_class(self) -> None:
        assert inspect.isclass(CodingProfileRunner)

    @pytest.mark.parametrize("fn", [execute_coding_profile, prepare_coding_profile])
    def test_entry_points_callable_with_single_ctx_arg(self, fn) -> None:
        # Both `execute_coding_profile(ctx)` and `prepare_coding_profile(ctx)`
        # take ctx as the first positional argument. Lock the shape.
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        assert len(params) >= 1
        assert params[0].name == "ctx"


EXPECTED_RUNNER_METHODS: tuple[str, ...] = (
    "prepare",
    "execute",
)


class TestCodingProfileRunnerMethods:
    @pytest.mark.parametrize("name", EXPECTED_RUNNER_METHODS)
    def test_runner_exposes_prepare_and_execute(self, name: str) -> None:
        assert hasattr(CodingProfileRunner, name), (
            f"CodingProfileRunner lost method `{name}`."
        )
        assert callable(getattr(CodingProfileRunner, name))


class TestCodingHandlerPureHelperBehavior:
    def test_build_error_result_shape(self) -> None:
        result = handler._build_error_result("oops", "TEST_CODE")
        assert result.summary == "oops"
        assert result.error is not None
        assert result.error.code == "TEST_CODE"

    def test_build_blocked_result_shape(self) -> None:
        result = handler._build_blocked_result("blocked", "TEST_CODE")
        assert result.summary == "blocked"
        # Blocked vs error is signaled by status, not by presence of error.
        from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_BLOCKED

        assert result.status == BRAIN_ACTION_STATUS_BLOCKED

    def test_build_tool_specs_returns_a_list(self) -> None:
        specs = handler._build_tool_specs(frozenset())
        assert isinstance(specs, list)

    def test_build_tool_specs_encodes_file_vs_shell_scaffolding_boundary(self) -> None:
        specs = handler._build_tool_specs(frozenset({"file.write", "exec.run"}))
        by_name = {spec.name: spec for spec in specs}

        assert "parent directories" in by_name["file.write"].description
        assert "scaffold" in by_name["file.write"].description.lower()
        assert "structured file tools" in by_name["exec.run"].description
        assert "directories" in by_name["exec.run"].description

    def test_build_tool_specs_uses_runtime_schema_when_available(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "workdir": {"type": "string"},
            },
            "required": ["command"],
            "additionalProperties": False,
        }
        with (
            patch.object(
                coding_runtime,
                "_runner_and_profile_from_context",
                return_value=(object(), None),
            ),
            patch.object(
                coding_runtime,
                "collect_runtime_tool_schemas",
                return_value=[
                    {
                        "name": "exec.run",
                        "parameters": schema,
                    }
                ],
            ),
        ):
            specs = handler._build_tool_specs(frozenset({"exec.run"}), ctx=object())

        [spec] = specs
        assert spec.input_schema == schema
        assert "path/cwd/working_directory" in spec.description

    def test_verify_phase_allowed_tools_drop_mutating_writers(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Ship a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"

        allowed = runner._allowed_tools_for_current_phase(
            default_allowed_tools=frozenset(
                {
                    "file.write",
                    "code.patch",
                    "file.read",
                    "file.read_range",
                    "exec.run",
                    "exec.list",
                }
            )
        )

        assert "file.write" not in allowed
        assert "code.patch" not in allowed
        assert "file.read" in allowed
        assert "file.read_range" in allowed
        assert "exec.run" in allowed
        assert "exec.list" in allowed

    def test_verify_phase_instruction_is_read_only(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Ship a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"

        runner._append_phase_instruction()

        prompt = runner._loop_state.messages[-1].content
        assert "Verification is read-only" in prompt
        assert "do not modify files or apply patches" in prompt
        assert "`file.read` or `file.read_range` first" in prompt


class TestCodingVerificationReserve:
    def test_file_read_counts_as_verification_candidate(self) -> None:
        runner = CodingProfileRunner()
        command = ToolCommand(
            title="read file",
            tool_name="file.read",
            args={"path": "/tmp/project.py"},
        )
        action_result = ActionResult(
            command_id="cmd-1",
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary="read ok",
        )

        runner._record_verifier_candidate(command, action_result)

        payload = runner._loop_state.scratchpad["coding.last_verifier_candidate"]
        assert payload["command"]["tool_name"] == "file.read"
        assert runner._has_verifier_candidate() is True

    @pytest.mark.parametrize(
        "termination_reason",
        (
            ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            ADAPTIVE_TERM_CIRCULAR_PATTERN,
            ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ),
    )
    def test_reserved_verification_step_continues_after_retryable_terminal_stop(
        self,
        termination_reason: str,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = SimpleNamespace(
            current_phase="implement",
            next_phase_name=lambda: "verify",
        )
        runner._loop_state.messages = [
            Message(role="assistant", content="old context"),
            Message(role="system", content="budget finalization system"),
            Message(role="assistant", content="<step1>Create files</step1>"),
        ]
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "code.patch", "ok": True},
            ],
            "budget_answer_only_restore_index": 1,
            "budget_answer_only_finalization_rejected_text": "<step1>Create files</step1>",
            "budget_answer_only_finalization_forced": True,
            "coding.pending_continue": True,
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=0,
                    a2a_calls=0,
                    tokens=1000,
                    time_ms=10000,
                )
            ),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=termination_reason,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "exec.run"}),
        )

        assert (
            runner._maybe_continue_with_verification_reserve(ctx, outcome=outcome)
            is True
        )
        assert ctx.state.budgets_remaining.tool_calls == 1
        assert runner._loop_state.scratchpad["coding.verification_reserve_used"] is True
        assert "budget_answer_only_restore_index" not in runner._loop_state.scratchpad
        assert "coding.pending_continue" not in runner._loop_state.scratchpad
        assert len(runner._loop_state.messages) == 2
        assert runner._loop_state.messages[0].content == "old context"
        assert (
            "reserved final tool step for verification only"
            in runner._loop_state.messages[-1].content
        )
        assert "preferring" in runner._loop_state.messages[-1].content.lower()
        assert "`file.read`" in runner._loop_state.messages[-1].content

    def test_final_answer_reserve_retries_after_verification_stub(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = SimpleNamespace(
            current_phase="verify",
            next_phase_name=lambda: None,
        )
        runner._last_verifier_candidate_payload = {
            "command": {"tool_name": "file.read"},
            "action_result": {"summary": "read ok"},
        }
        runner._loop_state.messages = [
            Message(role="user", content="Use the exact label `result:`."),
            Message(role="assistant", content="verification prompt"),
        ]
        runner._loop_state.scratchpad = {
            "budget_answer_only_restore_index": 1,
            "coding.last_verifier_candidate": dict(
                runner._last_verifier_candidate_payload
            ),
            "coding.pending_continue": True,
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason="final_text",
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.read"}),
            final_text="Verification step: read back loopcalc.py.",
        )

        assert runner._maybe_continue_with_final_answer_reserve(ctx, outcome=outcome)
        assert runner._loop_state.scratchpad["coding.final_answer_reserve_used"] is True
        assert "coding.pending_continue" not in runner._loop_state.scratchpad
        assert len(runner._loop_state.messages) == 2
        assert (
            runner._loop_state.messages[0].content == "Use the exact label `result:`."
        )
        assert "Do not call any tools" in runner._loop_state.messages[-1].content

    def test_final_answer_reserve_retries_after_verifier_incomplete_failure(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = SimpleNamespace(
            current_phase="verify",
            next_phase_name=lambda: None,
        )
        runner._last_verifier_candidate_payload = {
            "command": {"tool_name": "file.read"},
            "action_result": {"summary": "read ok"},
        }
        runner._loop_state.messages = [
            Message(role="user", content="Use the exact label `result:`."),
            Message(role="assistant", content="verifier answer"),
        ]
        runner._loop_state.scratchpad = {
            "budget_answer_only_restore_index": 1,
            "coding.last_verifier_candidate": dict(
                runner._last_verifier_candidate_payload
            ),
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_TOOL_FAILURE,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.read"}),
            final_text="Readback complete.",
            action_result=handler._build_error_result(
                "Typed verifier did not confirm coding completion.",
                "coding_verifier_incomplete",
            ),
            error_message="Typed verifier did not confirm coding completion.",
        )

        assert runner._maybe_continue_with_final_answer_reserve(ctx, outcome=outcome)
        assert runner._loop_state.scratchpad["coding.final_answer_reserve_used"] is True
        assert "Do not call any tools" in runner._loop_state.messages[-1].content

    def test_verify_closeout_reserve_promotes_verify_with_existing_readback(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._last_verifier_candidate_payload = {
            "command": {"tool_name": "file.read"},
            "action_result": {"summary": "read ok"},
        }
        runner._loop_state.messages = [
            Message(
                role="user", content="Use exact labels `design:` and `validation:`."
            ),
            Message(role="assistant", content="budget finalization draft"),
        ]
        runner._loop_state.scratchpad = {
            "coding.last_verifier_candidate": dict(
                runner._last_verifier_candidate_payload
            ),
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
            ],
            "budget_answer_only_restore_index": 1,
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "file.read"}),
            final_text="Implementation mostly complete.",
        )

        assert runner._maybe_continue_with_verify_closeout_reserve(ctx, outcome=outcome)
        assert runner._coding_plan.current_phase == "verify"
        assert runner._loop_state.scratchpad["coding.final_answer_reserve_used"] is True
        assert "Do not call any tools" in runner._loop_state.messages[-1].content

    def test_reserved_verification_step_also_works_inside_verify_phase(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.messages = [
            Message(role="assistant", content="old context"),
            Message(role="system", content="budget finalization system"),
            Message(role="assistant", content="<step1>Read files</step1>"),
        ]
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
            ],
            "budget_answer_only_restore_index": 1,
            "budget_answer_only_finalization_rejected_text": "<step1>Read files</step1>",
            "budget_answer_only_finalization_forced": True,
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                budgets_remaining=BudgetCounters(
                    ticks=10,
                    tool_calls=0,
                    a2a_calls=0,
                    tokens=1000,
                    time_ms=10000,
                )
            ),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "file.read", "exec.run"}),
        )

        assert (
            runner._maybe_continue_with_verification_reserve(ctx, outcome=outcome)
            is True
        )
        assert runner._loop_state.scratchpad["coding.verification_reserve_used"] is True
        assert len(runner._loop_state.messages) == 2
        assert runner._loop_state.messages[0].content == "old context"
        assert (
            "reserved final tool step for verification only"
            in runner._loop_state.messages[-1].content
        )
        assert "`file.read`" in runner._loop_state.messages[-1].content

    def test_verify_disallowed_writer_becomes_read_only_verification_retry(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_DISALLOWED_TOOL,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.read", "file.read_range", "exec.run"}),
            error_message="act_profile_coding does not allow tool 'file.write'.",
            tool_name="file.write",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        assert runner._loop_state.scratchpad["coding.verification_reserve_used"] is True
        assert "Verification is read-only" in runner._loop_state.messages[-1].content

    def test_verify_disallowed_writer_does_not_requeue_final_answer_reserve(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.scratchpad = {
            "coding.final_answer_reserve_used": True,
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_DISALLOWED_TOOL,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.read", "file.read_range", "exec.run"}),
            error_message="act_profile_coding does not allow tool 'file.write'.",
            tool_name="file.write",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "done"
        assert runner._loop_state.scratchpad["coding.final_answer_reserve_used"] is True
        assert "coding.verification_reserve_used" not in runner._loop_state.scratchpad
        assert "result: reserved final closeout was interrupted" in result.message

    def test_final_answer_reserve_disallowed_writer_salvages_final_summary(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.messages = [
            Message(
                role="user",
                content="Finish with `files changed:` and the exact label `result:`.",
            )
        ]
        runner._loop_state.scratchpad = {
            "coding.final_answer_reserve_used": True,
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "pkg/main.py"},
                }
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            evaluate_turn_closure=lambda **kwargs: None,
            apply_closure_judgment=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_DISALLOWED_TOOL,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset(),
            error_message="act_profile_coding does not allow tool 'file.write'.",
            tool_name="file.write",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "done"
        assert "files changed: pkg/main.py" in result.message
        assert "result: reserved final closeout was interrupted" in result.message

    def test_final_answer_reserve_budget_exhausted_salvages_final_summary(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.messages = [
            Message(
                role="user",
                content="Finish with `files changed:` and the exact label `result:`.",
            )
        ]
        runner._loop_state.scratchpad = {
            "coding.final_answer_reserve_used": True,
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "pkg/main.py"},
                }
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            evaluate_turn_closure=lambda **kwargs: None,
            apply_closure_judgment=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset(),
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "done"
        assert "files changed: pkg/main.py" in result.message
        assert "result: reserved final closeout was interrupted" in result.message

    def test_final_answer_reserve_blocked_cap_salvages_final_summary(self) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 1
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.termination_reason = "blocked_cap"
        runner._loop_state.messages = [
            Message(
                role="user",
                content="Finish with `files changed:` and the exact label `result:`.",
            )
        ]
        runner._loop_state.scratchpad = {
            "coding.final_answer_reserve_used": True,
            "coding.self_corrections": 1,
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": "pkg/main.py"},
                }
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            evaluate_turn_closure=lambda **kwargs: None,
            apply_closure_judgment=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_TOOL_FAILURE,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset(),
            error_message="act_profile_coding does not allow tool 'file.write'.",
            action_result=handler._build_error_result(
                "act_profile_coding does not allow tool 'file.write'.",
                "coding_disallowed_tool",
            ),
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "done"
        assert "files changed: pkg/main.py" in result.message
        assert "result: reserved final closeout was interrupted" in result.message

    def test_advance_plan_after_phase_blocks_at_self_correction_cap(self) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._loop_state.messages = [
            Message(
                role="tool",
                content=json.dumps({"status": "failed", "summary": "pytest failed"}),
            )
        ]
        runner._loop_state.scratchpad = {"coding.self_corrections": 2}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "exec.run"}),
        )

        assert runner._advance_plan_after_phase(ctx, outcome=outcome) is False
        assert runner._loop_state.termination_reason == "blocked_cap"
        assert "coding.pending_continue" not in runner._loop_state.scratchpad

    def test_advance_plan_after_phase_requires_mutating_implementation_tool(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.requires_file_change = True
        runner._loop_state.scratchpad = {}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: emitted.append(dict(kwargs)),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "exec.run"}),
        )

        assert runner._advance_plan_after_phase(ctx, outcome=outcome) is False
        assert runner._coding_plan.current_phase == "implement"
        assert runner._loop_state.scratchpad["coding.verify_gate_blocks"] == 1
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert (
            runner._loop_state.scratchpad["coding.required_write_direct_tool"]
            == "file.write"
        )
        direct_tool_turn = runner._loop_state.direct_tool_turn
        assert direct_tool_turn is not None
        assert direct_tool_turn.requested_tool_names == ("file.write",)
        assert direct_tool_turn.match_by_name_only is True
        assert "file.write" in runner._loop_state.messages[-1].content
        assert "code.patch" in runner._loop_state.messages[-1].content
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "missing_implementation_write"
            for status in emitted
        )

    def test_advance_plan_after_phase_allows_read_only_plan_without_write(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Explain this module.",
            include_verify=True,
            requires_file_change=False,
        )
        runner._loop_state.scratchpad = {}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.read", "exec.run"}),
        )

        assert runner._advance_plan_after_phase(ctx, outcome=outcome) is True
        assert runner._coding_plan.current_phase == "verify"
        assert "coding.verify_gate_blocks" not in runner._loop_state.scratchpad

    def test_advance_plan_after_phase_caps_missing_implementation_tool(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 1
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.requires_file_change = True
        runner._loop_state.scratchpad = {}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "exec.run"}),
        )

        assert runner._advance_plan_after_phase(ctx, outcome=outcome) is False
        assert runner._loop_state.termination_reason == CODING_TERM_VERIFY_CAP_EXCEEDED
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )

    def test_final_text_requires_mutating_tool_for_file_change_plan(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.requires_file_change = True
        runner._loop_state.scratchpad = {}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: emitted.append(dict(kwargs)),
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_FINAL_TEXT,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "exec.run"}),
            final_text="result: done",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        assert runner._coding_plan.current_phase == "implement"
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert "file.write" in runner._loop_state.messages[-1].content
        assert "code.patch" in runner._loop_state.messages[-1].content
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "missing_implementation_write"
            for status in emitted
        )

    def test_final_text_uses_scratchpad_required_file_change_when_plan_loses_flag(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.",
            include_verify=True,
            requires_file_change=False,
        )
        runner._loop_state.scratchpad = {"coding.requires_file_change": True}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: emitted.append(dict(kwargs)),
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_FINAL_TEXT,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "exec.run"}),
            final_text="result: done",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )

    def test_final_text_uses_scratchpad_required_file_change_when_plan_is_missing(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = None
        runner._loop_state.scratchpad = {"coding.requires_file_change": True}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: emitted.append(dict(kwargs)),
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_FINAL_TEXT,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "exec.run"}),
            final_text="result: done",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert "file.write" in runner._loop_state.messages[-1].content
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "missing_implementation_write"
            for status in emitted
        )

    def test_final_text_retries_when_model_prints_file_payload(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = None
        runner._loop_state.scratchpad = {"coding.requires_file_change": True}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_FINAL_TEXT,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "code.patch", "exec.run"}),
            final_text=json.dumps(
                {
                    "path": "test_project/pyproject.toml",
                    "content": "[project]\nname = \"test-project\"\n",
                }
            ),
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        retry = runner._loop_state.messages[-1].content
        assert "Do not print JSON" in retry
        assert "file.write" in retry
        assert "code.patch" in retry

    def test_context_sync_does_not_infer_file_change_requirement_without_plan(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = None
        runner._loop_state.scratchpad = {}
        ctx = SimpleNamespace(
            user_input="In the current directory, create a tiny Python module.",
            state=SimpleNamespace(goal="", task_backed_checkpoint_id=None),
            decision=SimpleNamespace(objective="", cwd="/tmp/project"),
            options=SimpleNamespace(),
        )

        runner._sync_coding_context(ctx)

        assert "coding.requires_file_change" not in runner._loop_state.scratchpad
        assert runner._loop_state.scratchpad["coding.cwd"] == "/tmp/project"

    def test_mutating_file_result_requires_existing_relative_path(
        self,
        tmp_path,
    ) -> None:
        runner = CodingProfileRunner()
        created = tmp_path / "tiny_func.py"
        created.write_text("def ok():\n    return True\n", encoding="utf-8")
        runner._loop_state.scratchpad = {
            "coding.cwd": str(tmp_path),
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True, "path": "tiny_func.py"},
            ],
        }

        assert runner._has_successful_mutating_file_result() is True

        runner._loop_state.scratchpad["adaptive.tool_results"] = [
            {"tool_name": "file.write", "ok": True, "path": "missing.py"},
        ]

        assert runner._has_successful_mutating_file_result() is False

    def test_mutating_file_result_accepts_runtime_final_path(
        self,
        tmp_path,
    ) -> None:
        runner = CodingProfileRunner()
        created = tmp_path / "runtime_written.py"
        created.write_text("VALUE = 1\n", encoding="utf-8")
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"final_path": str(created)},
                },
            ],
        }

        assert runner._has_successful_mutating_file_result() is True

        runner._loop_state.scratchpad["adaptive.tool_results"] = [
            {
                "tool_name": "file.write",
                "ok": True,
                "data": {"final_path": str(tmp_path / "missing.py")},
            },
        ]

        assert runner._has_successful_mutating_file_result() is False

    def test_mutating_file_result_without_path_keeps_legacy_success(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
            ],
        }

        assert runner._has_successful_mutating_file_result() is True

    def test_duplicate_tool_stop_requires_mutating_tool_for_file_change_plan(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.", include_verify=True)
        runner._coding_plan.requires_file_change = True
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.list_dir", "ok": True},
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: emitted.append(dict(kwargs)),
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "file.list_dir", "exec.run"}),
            error_message="duplicate tool batch",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        assert runner._coding_plan.current_phase == "implement"
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert "file.write" in runner._loop_state.messages[-1].content
        assert "code.patch" in runner._loop_state.messages[-1].content
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "missing_implementation_write"
            for status in emitted
        )

    def test_circular_tool_stop_uses_user_file_write_request_as_write_gate(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = None
        request = Message(
            role="user",
            content=(
                "Implement it using file.write/file.read in the current "
                "directory and validate by reading back one created file."
            ),
        )
        runner._loop_state.messages = []
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.list_dir", "ok": True},
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: emitted.append(dict(kwargs)),
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_CIRCULAR_PATTERN,
            state=AdaptiveToolLoopState(messages=[request]),
            allowed_tools=frozenset({"file.write", "file.read", "file.list_dir"}),
            error_message="circular pattern",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert "file.write" in runner._loop_state.messages[-1].content
        assert runner._loop_state.direct_tool_turn is not None
        assert runner._loop_state.direct_tool_turn.requested_tool_names == ("file.write",)
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "missing_implementation_write"
            for status in emitted
        )

    def test_budget_exhausted_before_file_write_uses_user_request_write_gate(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = None
        request = Message(
            role="user",
            content=(
                "Implement a tiny package using file.write/file.read and "
                "validate by reading back one created file."
            ),
        )
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.find", "ok": True},
                {"tool_name": "file.list_dir", "ok": True},
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: emitted.append(dict(kwargs)),
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_BUDGET_EXHAUSTED,
            state=AdaptiveToolLoopState(messages=[request]),
            allowed_tools=frozenset({"file.write", "file.read", "file.find"}),
            error_message="budget exhausted",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "continue"
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert "file.write" in runner._loop_state.messages[-1].content
        assert runner._loop_state.direct_tool_turn is not None
        assert runner._loop_state.direct_tool_turn.requested_tool_names == ("file.write",)
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "missing_implementation_write"
            for status in emitted
        )

    def test_budget_exhausted_after_file_write_returns_labeled_evidence_closeout(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        created = "wc_cli.py"
        runner._loop_state.messages = [
            Message(
                role="user",
                content=(
                    "Implement it with file.write/file.read. Close with "
                    "`design:`, `implementation:`, `validation:`, and `next steps:`."
                ),
            )
        ]
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {
                    "tool_name": "file.write",
                    "ok": True,
                    "data": {"path": created},
                },
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            evaluate_turn_closure=lambda **kwargs: None,
            apply_closure_judgment=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_BUDGET_EXHAUSTED,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "file.read"}),
            error_message="budget exhausted",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "done"
        message = str(result.message or "").lower()
        assert "design:" in message
        assert "implementation:" in message
        assert "validation:" in message
        assert "next steps:" in message
        assert "wc_cli.py" in result.message

    def test_final_text_allows_read_only_plan_without_write(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Explain this module.", include_verify=True)
        runner._coding_plan.requires_file_change = False
        runner._loop_state.scratchpad = {}
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
            evaluate_turn_closure=lambda **kwargs: None,
            apply_closure_judgment=lambda **kwargs: None,
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_FINAL_TEXT,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.read"}),
            final_text="result: explanation",
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "done"
        assert result.message == "result: explanation"
        assert "coding.verify_gate_blocks" not in runner._loop_state.scratchpad

    def test_final_answer_reserve_detects_missing_requested_markers(self) -> None:
        runner = CodingProfileRunner()
        runner._loop_state.messages = [
            Message(
                role="user",
                content=(
                    "Close with the exact labels `design:`, `validation:`, and "
                    "`next steps:`."
                ),
            )
        ]

        assert runner._missing_requested_final_markers("design: done") is True
        assert (
            runner._missing_requested_final_markers(
                "design: done\nvalidation: passed\nnext steps: none"
            )
            is False
        )
