from __future__ import annotations

import json
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
)
from openminion.modules.brain.loop.strategies.coding import handler
from openminion.modules.brain.loop.strategies.coding import context_adapter
from openminion.modules.brain.loop.strategies.coding import runtime as coding_runtime
from openminion.modules.brain.loop.strategies.coding.context_adapter import (
    _CodingLoopContextAdapter,
)
from openminion.modules.brain.loop.strategies.coding.handler import (
    CodingMode,
    CodingProfileRunner,
    execute_coding_profile,
    prepare_coding_profile,
)
from openminion.modules.brain.loop.strategies.coding.plan import CodingPlan
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    ADAPTIVE_TERM_FINALIZATION_BLOCKED,
    ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
    ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
    ADAPTIVE_TERM_LLM_ERROR,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopState,
)
from openminion.modules.brain.loop.tools.contracts import (
    CommandExecutionOutcome,
    PreparedToolDispatch,
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
        assert "complete target file path" in by_name["file.write"].description
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

    def test_build_tool_specs_projects_targets_only_for_verify_candidates(self) -> None:
        specs = handler._build_tool_specs(
            frozenset({"file.read", "file.read_range", "exec.run", "exec.poll"}),
            verification_targets={
                "criterion": ("criterion-http",),
                "deliverable": ("deliverable-page",),
            },
        )
        by_name = {spec.name: spec for spec in specs}

        for tool_name in ("file.read", "file.read_range", "exec.run"):
            schema = by_name[tool_name].input_schema
            assert "verification_target_kind" in schema["required"]
            assert "verification_target_id" in schema["required"]
            assert schema["properties"]["verification_target_id"]["enum"] == [
                "criterion-http",
                "deliverable-page",
            ]
        assert (
            "verification_target_id"
            not in by_name["exec.poll"].input_schema["properties"]
        )

        [ordinary] = handler._build_tool_specs(frozenset({"exec.run"}))
        assert "verification_target_id" not in ordinary.input_schema["properties"]

    def test_verify_phase_allowed_tools_drop_mutating_writers(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Ship a tiny CLI.", include_verify=True
        )
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
        runner._coding_plan = CodingPlan.fallback(
            "Ship a tiny CLI.", include_verify=True
        )
        runner._coding_plan.current_phase = "verify"

        runner._append_phase_instruction(
            SimpleNamespace(state=SimpleNamespace(goal=None))
        )

        prompt = runner._loop_state.messages[-1].content
        assert "Verification is read-only" in prompt
        assert "do not modify files or apply patches" in prompt
        assert "`file.read` or `file.read_range` first" in prompt

    def test_verification_binding_survives_direct_and_prepared_dispatch(self) -> None:
        seen: list[ToolCommand] = []
        callbacks: list[ToolCommand] = []

        def execute_command(*, state, command, logger, include_reflect):
            del state, logger, include_reflect
            seen.append(command)
            return CommandExecutionOutcome(
                approved_command=command,
                action_result=ActionResult(
                    command_id=command.command_id,
                    status=BRAIN_ACTION_STATUS_SUCCESS,
                    summary="ok",
                ),
            )

        direct_executor = SimpleNamespace(execute_command=execute_command)
        ctx = SimpleNamespace(
            state=SimpleNamespace(),
            command_executor=direct_executor,
            logger=None,
        )
        command = ToolCommand(
            title="verify",
            tool_name="exec.run",
            args={
                "argv": ["pytest", "-q"],
                "verification_target_kind": "criterion",
                "verification_target_id": "criterion-tests",
            },
        )
        with patch.object(
            context_adapter,
            "runner_from_context",
            return_value=SimpleNamespace(session_api=None, options=None),
        ):
            adapter = _CodingLoopContextAdapter(
                ctx,
                on_command_result=lambda approved, result: callbacks.append(approved),
            )
            adapter.execute_command(command=command)

        assert seen[0].args == {"argv": ["pytest", "-q"]}
        assert seen[0].verification_target_kind == "criterion"
        assert seen[0].verification_target_id == "criterion-tests"
        assert callbacks == seen

        def prepare_tool_dispatch(*, state, command, logger, include_reflect):
            del state, logger, include_reflect
            seen.append(command)
            return PreparedToolDispatch(
                approved_command=command,
                original_command=command,
                command_id=command.command_id,
                tool_name=command.tool_name,
                validated_args=dict(command.args),
                session_id="session-1",
                trace_id="trace-1",
                agent_id="agent-1",
                lineage={},
                permission_mode="default",
                payload={},
            )

        prepared_executor = SimpleNamespace(
            prepare_tool_dispatch=prepare_tool_dispatch,
            execute_prepared_tool_dispatch=lambda **kwargs: None,
            finalize_tool_result=lambda **kwargs: None,
        )
        ctx.command_executor = prepared_executor
        with patch.object(
            context_adapter,
            "runner_from_context",
            return_value=SimpleNamespace(session_api=None, options=None),
        ):
            prepared_adapter = _CodingLoopContextAdapter(ctx)
            prepared = prepared_adapter.prepare_tool_dispatch(command=command)

        assert prepared.approved_command.args == {"argv": ["pytest", "-q"]}
        assert prepared.approved_command.verification_target_kind == "criterion"
        assert prepared.approved_command.verification_target_id == "criterion-tests"

    @pytest.mark.parametrize(
        "termination_reason",
        (ADAPTIVE_TERM_FINALIZATION_BLOCKED, ADAPTIVE_TERM_FINALIZATION_INCOMPLETE),
    )
    def test_incomplete_finalization_remains_resumable(
        self,
        termination_reason: str,
    ) -> None:
        runner = CodingProfileRunner()
        runner._finalize_checkpoint = lambda *args, **kwargs: None
        ctx = SimpleNamespace(
            state=SimpleNamespace(),
            respond=lambda **kwargs: SimpleNamespace(
                kind="assistant",
                working_state=ctx.state,
                **kwargs,
            ),
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=termination_reason,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset(),
            final_text="Implementation needs another turn.",
            finalization_status={"status": "incomplete"},
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "waiting_user"
        assert result.message == "Implementation needs another turn."

    @pytest.mark.parametrize(
        "termination_reason, expected_code",
        (
            (
                ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
                "coding_finalization_contract_missing",
            ),
            (
                ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
                "coding_direct_tool_closure_failed",
            ),
        ),
    )
    def test_finalization_integrity_failures_are_explicit_errors(
        self,
        termination_reason: str,
        expected_code: str,
    ) -> None:
        runner = CodingProfileRunner()
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                budgets_remaining=SimpleNamespace(tool_calls=1, tokens=1),
                llm_calls_used=0,
                llm_calls_max=1,
            )
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=termination_reason,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset(),
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "error"
        assert result.action_result.error is not None
        assert result.action_result.error.code == expected_code

    def test_missing_finalization_contract_at_budget_boundary_is_resumable(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._loop_state.tool_calls_made = ["file.write"]
        runner._finalize_checkpoint = lambda *args, **kwargs: None
        ctx = SimpleNamespace(
            state=SimpleNamespace(
                budgets_remaining=SimpleNamespace(tool_calls=0, tokens=1),
                llm_calls_used=0,
                llm_calls_max=1,
            )
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset(),
        )

        result = runner._result_from_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result.status == "waiting_user"
        assert "Continue in a new turn to resume" in result.message
        assert result.action_result.error is not None
        assert result.action_result.error.code == "coding_budget_exhausted"


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

    def test_directory_listing_replaces_failed_criterion_evidence(self) -> None:
        runner = CodingProfileRunner()
        failed_command = ToolCommand(
            title="list files with shell",
            tool_name="exec.run",
            args={"argv": ["ls"]},
            verification_target_kind="criterion",
            verification_target_id="files-exist",
        )
        runner._record_verifier_candidate(
            failed_command,
            ActionResult(
                command_id=failed_command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary="use file.list_dir",
            ),
        )
        listing_command = ToolCommand(
            title="list files",
            tool_name="file.list_dir",
            args={"path": "."},
            verification_target_kind="criterion",
            verification_target_id="files-exist",
        )
        runner._record_verifier_candidate(
            listing_command,
            ActionResult(
                command_id=listing_command.command_id,
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="listed files",
                outputs={"entries": [{"name": "greet.py", "type": "file"}]},
            ),
        )

        command, action_result = runner._bound_verifier_candidates()[0]
        assert command.tool_name == "file.list_dir"
        assert action_result.status == BRAIN_ACTION_STATUS_SUCCESS

    def test_file_write_binds_an_exact_deliverable_path(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = SimpleNamespace(
            verifier_goal=SimpleNamespace(
                deliverables=[
                    SimpleNamespace(deliverable_id="test_section_summary.py")
                ]
            )
        )
        command = ToolCommand(
            title="write tests",
            tool_name="file.write",
            args={"path": "/tmp/project/test_section_summary.py", "content": ""},
        )

        runner._record_verifier_candidate(
            command,
            ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="wrote tests",
                outputs={"path": "/tmp/project/test_section_summary.py"},
            ),
        )

        candidate = runner._loop_state.scratchpad["coding.verifier_candidates"]
        assert set(candidate) == {"deliverable:test_section_summary.py"}

    def test_unbound_verifier_binds_only_remaining_typed_target(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = SimpleNamespace(
            verifier_goal=SimpleNamespace(
                success_criteria=[SimpleNamespace(criterion_id="tests-pass")],
                deliverables=[SimpleNamespace(deliverable_id="module.py")],
            )
        )
        runner._loop_state.scratchpad["coding.verifier_candidates"] = {
            "deliverable:module.py": {"already": "bound"}
        }
        command = ToolCommand(
            title="run tests",
            tool_name="exec.run",
            args={"argv": ["pytest", "-q"]},
        )

        runner._record_verifier_candidate(
            command,
            ActionResult(
                command_id=command.command_id,
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="tests passed",
                outputs={"exit_code": 0},
            ),
        )

        candidate = runner._loop_state.scratchpad["coding.verifier_candidates"]
        assert set(candidate) == {"deliverable:module.py", "criterion:tests-pass"}
        bound, _result = runner._bound_verifier_candidates()[0]
        assert bound.verification_target_kind == "criterion"
        assert bound.verification_target_id == "tests-pass"

    def test_success_from_another_tool_does_not_clear_exec_failure(self) -> None:
        runner = CodingProfileRunner()
        failed_command = ToolCommand(
            title="run tests",
            tool_name="exec.run",
            args={"argv": ["pytest", "-q"]},
        )
        runner._record_verifier_candidate(
            failed_command,
            ActionResult(
                command_id=failed_command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary="tests failed",
            ),
        )
        runner._loop_state.scratchpad["coding.self_corrections"] = 1
        write_command = ToolCommand(
            title="fix source",
            tool_name="file.write",
            args={"path": "module.py", "content": "fixed"},
        )

        runner._record_verifier_candidate(
            write_command,
            ActionResult(
                command_id=write_command.command_id,
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="fixed source",
                outputs={"path": "module.py"},
            ),
        )

        assert "coding.unresolved_verifier_failure" in runner._loop_state.scratchpad

    def test_successful_mutation_allows_reverification_of_an_open_failure(self) -> None:
        runner = CodingProfileRunner()
        failed_command = ToolCommand(
            title="run tests",
            tool_name="exec.run",
            args={"argv": ["pytest", "-q"]},
        )
        runner._record_verifier_candidate(
            failed_command,
            ActionResult(
                command_id=failed_command.command_id,
                status=BRAIN_ACTION_STATUS_FAILED,
                summary="tests failed",
            ),
        )
        runner._loop_state.scratchpad["adaptive.tool_results"] = [
            {"tool_name": "exec.run", "ok": False},
            {"tool_name": "file.write", "ok": True},
        ]

        assert runner._latest_tool_failure_summary() == ""
        assert "coding.unresolved_verifier_failure" in runner._loop_state.scratchpad

    def test_failed_verifier_is_not_erased_by_later_success(self) -> None:
        runner = CodingProfileRunner()
        failed_command = ToolCommand(
            title="primary verifier",
            tool_name="exec.run",
            args={"argv": ["verify-primary"]},
        )
        failed_result = ActionResult(
            command_id=failed_command.command_id,
            status=BRAIN_ACTION_STATUS_FAILED,
            summary="primary verification failed",
            outputs={"exit_code": 1},
        )
        successful_command = ToolCommand(
            title="diagnostic",
            tool_name="exec.run",
            args={"argv": ["inspect-one-value"]},
        )
        successful_result = ActionResult(
            command_id=successful_command.command_id,
            status=BRAIN_ACTION_STATUS_SUCCESS,
            summary="diagnostic completed",
            outputs={"exit_code": 0},
        )

        runner._record_verifier_candidate(failed_command, failed_result)
        runner._record_verifier_candidate(successful_command, successful_result)

        unresolved = runner._loop_state.scratchpad["coding.unresolved_verifier_failure"]
        assert unresolved["command"]["args"] == {"argv": ["verify-primary"]}
        assert runner._latest_tool_failure_summary() == "primary verification failed"
        assert runner._loop_state.scratchpad["coding.last_verifier_candidate"][
            "command"
        ]["args"] == {"argv": ["inspect-one-value"]}

        restored = CodingProfileRunner()
        restored.restore_state(runner.snapshot_state())
        assert restored._latest_tool_failure_summary() == "primary verification failed"

        runner._record_autonomous_correction(
            SimpleNamespace(
                state=SimpleNamespace(task_backed_checkpoint_id=None),
                emit_status=lambda **kwargs: None,
            ),
            failure_summary="primary verification failed",
        )

        assert "coding.unresolved_verifier_failure" in runner._loop_state.scratchpad
        assert runner._loop_state.scratchpad["coding.self_corrections"] == 1

        runner._record_verifier_candidate(successful_command, successful_result)

        assert "coding.unresolved_verifier_failure" not in runner._loop_state.scratchpad

    def test_parallel_running_exec_polls_inherit_original_targets(self) -> None:
        runner = CodingProfileRunner()
        for session_id, target_kind, target_id in (
            ("execproc-1", "criterion", "criterion-tests"),
            ("execproc-2", "deliverable", "deliverable-report"),
        ):
            runner._record_verifier_candidate(
                ToolCommand(
                    title="run verifier",
                    tool_name="exec.run",
                    args={"argv": ["python", "-m", "pytest", "-q"]},
                    verification_target_kind=target_kind,
                    verification_target_id=target_id,
                ),
                ActionResult(
                    command_id=f"cmd-{session_id}",
                    status=BRAIN_ACTION_STATUS_SUCCESS,
                    summary="Command still running",
                    outputs={"status": "running", "session_id": session_id},
                ),
            )

        pending = runner._loop_state.scratchpad["coding.pending_verifier_sessions"]
        assert pending == {
            "execproc-1": {
                "verification_target_kind": "criterion",
                "verification_target_id": "criterion-tests",
            },
            "execproc-2": {
                "verification_target_kind": "deliverable",
                "verification_target_id": "deliverable-report",
            },
        }
        assert runner._has_verifier_candidate() is False

        runner._record_verifier_candidate(
            ToolCommand(
                title="poll first verifier",
                tool_name="exec.poll",
                args={"session_id": "execproc-1"},
                verification_target_kind="deliverable",
                verification_target_id="deliverable-report",
            ),
            ActionResult(
                command_id="cmd-poll-1",
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="tests passed",
                outputs={
                    "status": "exited",
                    "session_id": "execproc-1",
                    "exit_code": 0,
                },
            ),
        )
        first = runner._loop_state.scratchpad["coding.last_verifier_candidate"]
        assert first["command"]["verification_target_kind"] == "criterion"
        assert first["command"]["verification_target_id"] == "criterion-tests"
        assert set(
            runner._loop_state.scratchpad["coding.pending_verifier_sessions"]
        ) == {"execproc-2"}
        assert runner._has_verifier_candidate() is False

        runner._record_verifier_candidate(
            ToolCommand(
                title="poll second verifier",
                tool_name="exec.poll",
                args={"session_id": "execproc-2"},
            ),
            ActionResult(
                command_id="cmd-poll-2",
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="report created",
                outputs={
                    "status": "exited",
                    "session_id": "execproc-2",
                    "exit_code": 0,
                },
            ),
        )

        assert not runner._loop_state.scratchpad["coding.pending_verifier_sessions"]
        assert runner._has_verifier_candidate() is True
        candidates = runner._loop_state.scratchpad["coding.verifier_candidates"]
        assert set(candidates) == {
            "criterion:criterion-tests",
            "deliverable:deliverable-report",
        }

    def test_failed_exec_poll_preserves_original_target_for_terminal_poll(self) -> None:
        runner = CodingProfileRunner()
        runner._record_verifier_candidate(
            ToolCommand(
                title="run verifier",
                tool_name="exec.run",
                args={"argv": ["python", "-m", "pytest", "-q"]},
                verification_target_kind="criterion",
                verification_target_id="criterion-tests",
            ),
            ActionResult(
                command_id="cmd-run",
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="Command still running",
                outputs={"status": "running", "session_id": "execproc-1"},
            ),
        )
        poll = ToolCommand(
            title="poll verifier",
            tool_name="exec.poll",
            args={"session_id": "execproc-1"},
        )

        runner._record_verifier_candidate(
            poll,
            ActionResult(
                command_id="cmd-poll-failed",
                status="failed",
                summary="poll transport failed",
                outputs={"status": "error"},
            ),
        )

        assert (
            "execproc-1"
            in runner._loop_state.scratchpad["coding.pending_verifier_sessions"]
        )

        runner._record_verifier_candidate(
            poll,
            ActionResult(
                command_id="cmd-poll-terminal",
                status=BRAIN_ACTION_STATUS_SUCCESS,
                summary="tests passed",
                outputs={
                    "status": "exited",
                    "session_id": "execproc-1",
                    "exit_code": 0,
                },
            ),
        )

        assert not runner._loop_state.scratchpad["coding.pending_verifier_sessions"]
        command, _result = runner._bound_verifier_candidates()[0]
        assert command.verification_target_kind == "criterion"
        assert command.verification_target_id == "criterion-tests"

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

    def test_final_answer_reserve_does_not_classify_model_prose(self) -> None:
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
            state=SimpleNamespace(
                task_backed_checkpoint_id=None,
                goal="Use the exact label `result:`.",
            ),
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

        assert not runner._maybe_continue_with_final_answer_reserve(
            ctx, outcome=outcome
        )
        assert "coding.final_answer_reserve_used" not in runner._loop_state.scratchpad
        assert runner._loop_state.scratchpad["coding.pending_continue"] is True
        assert len(runner._loop_state.messages) == 2

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
            state=SimpleNamespace(
                task_backed_checkpoint_id=None,
                goal="Use the exact label `result:`.",
            ),
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
        assert "Original request:" in runner._loop_state.messages[-1].content
        assert "Use the exact label `result:`." in runner._loop_state.messages[-1].content

    def test_verify_closeout_reserve_promotes_verify_with_existing_readback(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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

    def test_verification_reserve_does_not_parse_llm_error_text(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
            ],
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
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset(),
            error_message="Model returned tool calls after tool_choice=none was enforced.",
        )

        assert not runner._maybe_continue_with_verification_reserve(
            ctx, outcome=outcome
        )
        assert "coding.verification_reserve_used" not in runner._loop_state.scratchpad

    def test_verify_disallowed_writer_becomes_read_only_verification_retry(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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

    def test_verify_disallowed_writer_fails_without_model_final_answer(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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

        assert result.status == "error"
        assert runner._loop_state.scratchpad["coding.final_answer_reserve_used"] is True
        assert "coding.verification_reserve_used" not in runner._loop_state.scratchpad
        assert "reserved final closeout" not in result.message

    def test_final_answer_reserve_disallowed_writer_does_not_invent_summary(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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

        assert result.status == "error"
        assert "files changed: pkg/main.py" not in result.message
        assert "reserved final closeout" not in result.message

    def test_final_answer_reserve_budget_exhausted_salvages_final_summary(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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

        assert result.status == "waiting_user"
        assert "budget exhausted" in str(result.message or "")

    def test_budget_exhausted_after_file_write_remains_resumable(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.scratchpad = {
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

        assert result.status == "waiting_user"
        assert "Continue in a new turn to resume." in str(result.message or "")
        assert "successful file writes were closed" not in str(result.message or "")

    def test_final_answer_reserve_budget_exhausted_does_not_salvage_missing_validation(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
        runner._coding_plan.current_phase = "verify"
        runner._loop_state.messages = [
            Message(
                role="user",
                content=(
                    "Use file.write for files, run focused validation with "
                    "exec.run, and finish with the exact label `result:`."
                ),
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

        assert result.status != "done"
        assert "successful file mutations" not in str(result.message or "")

    def test_final_answer_reserve_blocked_cap_remains_blocked(self) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 1
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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

        assert result.status == "waiting_user"
        assert "files changed: pkg/main.py" not in result.message
        assert "reserved final closeout" not in result.message

    def test_advance_plan_after_phase_blocks_at_self_correction_cap(self) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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

    def test_advance_plan_after_phase_allows_one_missing_write_correction_at_cap_one(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 1
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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
        assert runner._loop_state.termination_reason != CODING_TERM_VERIFY_CAP_EXCEEDED
        assert runner._coding_plan.current_phase == "implement"
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert (
            runner._loop_state.scratchpad["coding.required_write_direct_tool"]
            == "file.write"
        )

    def test_advance_plan_after_phase_allows_one_missing_write_correction_at_zero_cap(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 0
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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
        assert runner._loop_state.termination_reason != CODING_TERM_VERIFY_CAP_EXCEEDED
        assert (
            runner._loop_state.scratchpad["coding.required_write_direct_tool"]
            == "file.write"
        )

    def test_advance_plan_after_phase_caps_repeated_missing_write_after_allowed_correction(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 1
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
        runner._coding_plan.requires_file_change = True
        runner._loop_state.scratchpad = {"coding.verify_gate_blocks": 1}
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

    def test_final_text_requires_mutating_tool_for_file_change_plan(
        self,
    ) -> None:
        emitted: list[dict[str, object]] = []
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "missing_implementation_write"
            for status in emitted
        )

    def test_missing_write_retry_continues_inside_same_coding_turn(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 2
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
        runner._coding_plan.requires_file_change = True
        runner._loop_state.scratchpad = {}
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
            allowed_tools=frozenset({"file.write", "exec.run"}),
            final_text="I'll create the files now.",
        )

        result = runner._handle_iteration_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result is None
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "missing_implementation_write"
        )
        assert (
            runner._loop_state.scratchpad["coding.required_write_direct_tool"]
            == "file.write"
        )
        assert runner._loop_state.direct_tool_turn is not None
        assert "file.write" in runner._loop_state.messages[-1].content

    def test_successful_mutation_adds_verify_to_read_only_plan(self) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback("Build a tiny CLI.")
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True},
            ],
        }
        ctx = SimpleNamespace(
            state=SimpleNamespace(task_backed_checkpoint_id=None),
            emit_status=lambda **kwargs: None,
        )
        outcome = AdaptiveToolLoopOutcome(
            profile_name="coding_v1",
            mode_name="act_coding",
            termination_reason=CODING_TERM_FINAL_TEXT,
            state=runner._as_adaptive_state(runner._loop_state),
            allowed_tools=frozenset({"file.write", "file.read"}),
            final_text="Implemented the change.",
        )

        result = runner._handle_iteration_outcome(
            ctx,
            outcome=outcome,
            allowed_tools=outcome.allowed_tools,
        )

        assert result is None
        assert runner._coding_plan.requires_file_change is True
        assert runner._coding_plan.current_phase == "verify"
        assert [phase.name for phase in runner._coding_plan.phases] == [
            "implement",
            "verify",
        ]

    def test_initial_implement_phase_stages_file_writer_for_explicit_file_task(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Create a tiny Python function.",
            include_verify=True,
        )
        runner._coding_plan.requires_file_change = True
        runner._loop_state.messages = [
            Message(
                role="user",
                content=(
                    "Create a tiny Python function and one minimal check. Use "
                    "file tools for files."
                ),
            )
        ]

        runner._stage_initial_write_if_required()

        assert (
            runner._loop_state.scratchpad["coding.required_write_direct_tool"]
            == "file.write"
        )
        assert runner._loop_state.direct_tool_turn is not None
        assert runner._loop_state.direct_tool_turn.requested_tool_names == (
            "file.write",
        )

    def test_duplicate_tool_stop_gets_one_direct_write_retry_at_verify_cap(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._max_self_corrections = 1
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
        runner._coding_plan.requires_file_change = True
        runner._loop_state.scratchpad = {
            "adaptive.tool_results": [
                {"tool_name": "file.list_dir", "ok": True},
            ],
            "coding.verify_gate_blocks": 1,
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
        assert (
            runner._loop_state.scratchpad["coding.verify_gate_reason"]
            == "readonly_dead_end_missing_write"
        )
        assert (
            runner._loop_state.scratchpad["coding.required_write_direct_tool"]
            == "file.write"
        )
        assert runner._loop_state.direct_tool_turn is not None
        assert "file.write" in runner._loop_state.messages[-1].content

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

    def test_missing_write_retry_does_not_parse_model_final_text(
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
                    "content": '[project]\nname = "test-project"\n',
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
        assert "JSON" not in retry
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

    def test_successful_mutations_become_verifier_artifact_refs(self, tmp_path) -> None:
        first = tmp_path / "tiny_math.py"
        second = tmp_path / "test_tiny_math.py"
        first.write_text("def add_one(value): return value + 1\n", encoding="utf-8")
        second.write_text("def test_add_one(): assert True\n", encoding="utf-8")
        runner = CodingProfileRunner()
        runner._loop_state.scratchpad = {
            "coding.cwd": str(tmp_path),
            "adaptive.tool_results": [
                {"tool_name": "file.write", "ok": True, "path": first.name},
                {"tool_name": "file.write", "ok": True, "path": second.name},
                {"tool_name": "file.read", "ok": True, "path": first.name},
            ],
        }

        refs = runner._successful_mutating_artifact_refs()

        assert [ref.ref for ref in refs] == [first.name, second.name]

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
        runner._coding_plan = CodingPlan.fallback(
            "Build a tiny CLI.", include_verify=True
        )
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
            == "readonly_dead_end_missing_write"
        )
        assert "file.write" in runner._loop_state.messages[-1].content
        assert any(
            status.get("payload", {}).get("coding.verify_gate_reason")
            == "readonly_dead_end_missing_write"
            for status in emitted
        )

    def test_circular_tool_stop_does_not_infer_write_gate_from_prose(
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

        assert result.status == "waiting_user"
        assert "coding.verify_gate_reason" not in runner._loop_state.scratchpad
        assert runner._loop_state.direct_tool_turn is None
        assert not emitted

    def test_budget_exhausted_does_not_infer_write_gate_from_prose(
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

        assert result.status == "waiting_user"
        assert "coding.verify_gate_reason" not in runner._loop_state.scratchpad
        assert runner._loop_state.direct_tool_turn is None
        assert not emitted

    def test_budget_exhausted_after_file_write_stays_resumable(
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

        assert result.status == "waiting_user"
        message = str(result.message or "").lower()
        assert "budget exhausted" in message
        assert "continue in a new turn to resume" in message
        assert "result:" not in message

    def test_final_text_allows_read_only_plan_without_write(
        self,
    ) -> None:
        runner = CodingProfileRunner()
        runner._coding_plan = CodingPlan.fallback(
            "Explain this module.", include_verify=True
        )
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
