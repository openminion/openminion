"""Dispatch and state regressions for bounded identical read recovery."""

import json

import pytest

from openminion.modules.brain.loop.tools import (
    run_adaptive_tool_loop,
)
from openminion.modules.brain.loop.tools.contracts import semantic_batch_signature
from openminion.modules.brain.schemas import ActionResult, ToolCommand
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, UsageInfo
from tests.brain.tool_loops.test_engine import (
    _FakeRuntime,
    _LoopContext,
    _profile,
    _state,
    _tool_specs,
)


def call(i, *, name="file.read", path="a", **args):
    return ToolCall(id=f"call-{i}", name=name, arguments={"path": path, **args})


def reply(*calls):
    return LLMResponse(
        ok=True,
        provider="fixture",
        model="fixture",
        tool_calls=list(calls),
        output_text="" if calls else "done",
        usage=UsageInfo(input_tokens=3, output_tokens=2),
    )


def result(status="success", *, code=None, outputs=None, job=None):
    return CommandExecutionOutcome(
        approved_command=ToolCommand(
            tool_name="file.read", title="Read", args={"path": "a"}
        ),
        job=job,
        action_result=ActionResult(
            command_id="fixture",
            status=status,
            summary=status,
            outputs=outputs or {},
            error={"code": code, "message": code} if code else None,
        ),
    )


def failed(signal):
    if signal == "timeout_code":
        return result("failed", code="TIMEOUT")
    if signal == "timeout_status":
        return result("timeout")
    if signal == "status_retry":
        return result("retry")
    if signal == "structured_retryable":
        return result("failed", outputs={"_structured_retryable": True})
    if signal == "retry_after":
        return result("failed", outputs={"retry_after_ms": 1})
    return result("failed", outputs={"retryable": True})


def run(responses, outcomes, *, state=None, initial=None, allowed=None, **profile_args):
    names = allowed or {"file.read", "file.write", "exec.run", "payments.send"}
    ctx = _LoopContext(
        state=state or _state(tool_calls=20, tokens=2000, llm_calls_max=20),
        outcomes=outcomes,
    )
    rt = _FakeRuntime(responses=responses)
    outcome = run_adaptive_tool_loop(
        ctx,
        profile=_profile(
            max_iterations=10, allowed_tools=frozenset(names), **profile_args
        ),
        runtime=rt,
        model="fixture",
        initial_messages=[Message(role="user", content="Read the requested file.")],
        initial_state=initial,
        tool_specs=_tool_specs(*sorted(names)),
    )
    return ctx, rt, outcome


@pytest.mark.parametrize(
    "signal",
    [
        "timeout_code",
        "timeout_status",
        "status_retry",
        "retryable",
        "structured_retryable",
        "retry_after",
    ],
)
def test_transient_read_dispatches_again_and_keeps_ids_and_budgets(signal):
    ctx, rt, out = run(
        [reply(call(1)), reply(call(2)), reply()], [failed(signal), result()]
    )
    assert [c.command_id for c in ctx.commands] == ["call-1", "call-2"]
    assert ctx.commands[0].args == ctx.commands[1].args
    assert out.state.total_tool_calls == 2
    assert ctx.state.budgets_remaining.tool_calls == 18
    assert ctx.state.budgets_remaining.tokens == 1985
    assert out.state.llm_calls == len(rt.calls) == 3
    assert [m.tool_call_id for m in rt.calls[-1]["messages"] if m.role == "tool"] == [
        "call-1",
        "call-2",
    ]
    assert out.state.scratchpad["loop.cache_hits"] == 0
    assert (
        out.state.scratchpad["duplicate_read_redispatch_counts"][
            semantic_batch_signature([call(1)])
        ]
        == 1
    )


@pytest.mark.parametrize(
    "signal",
    [
        "timeout_code",
        "timeout_status",
        "status_retry",
        "retryable",
        "structured_retryable",
        "retry_after",
    ],
)
def test_transient_failure_stops_after_one_real_retry(signal):
    ctx, rt, out = run(
        [reply(call(i)) for i in range(12)], [failed(signal) for _ in range(12)]
    )
    assert len(ctx.commands) == out.state.total_tool_calls == 2
    assert out.termination_reason in {"duplicate_tool_calls", "circular_pattern"}
    assert len(rt.calls) < 10
    assert ctx.state.budgets_remaining.tool_calls == 18


@pytest.mark.parametrize(
    "status,code",
    [
        ("failed", "NOT_FOUND"),
        ("failed", "AUTH_FAILED"),
        ("blocked", "POLICY_DENIED"),
        ("needs_user", "CONFIRM_REQUIRED"),
        ("failed", "INVALID_ARGUMENT"),
        ("failed", "HTTP_403"),
        ("failed", "AUTH_ERROR"),
        ("failed", "POLICY_BLOCKED"),
    ],
)
@pytest.mark.parametrize("retry_flag", [False, True])
def test_permanent_and_authorization_failures_do_not_replay(status, code, retry_flag):
    ctx, _, out = run(
        [reply(call(i)) for i in range(12)],
        [
            result(status, code=code, outputs={"retryable": retry_flag})
            for _ in range(12)
        ],
    )
    assert len(ctx.commands) == 1
    assert out.state.total_tool_calls == 1
    assert out.termination_reason not in {"final_text", "confident_complete"}


@pytest.mark.parametrize(
    "outputs",
    [
        {},
        {"retryable": "false"},
        {"retry_after_ms": True},
        {"retry_after_ms": -1},
        {"retry_after_ms": "1"},
    ],
)
def test_unclassified_or_malformed_failure_does_not_replay(outputs):
    ctx, _, _ = run(
        [reply(call(i)) for i in range(12)],
        [result("failed", outputs=outputs) for _ in range(12)],
    )
    assert len(ctx.commands) == 1


@pytest.mark.parametrize(
    "name", ["file.write", "exec.run", "payments.send", "custom.read"]
)
def test_retry_signal_does_not_authorize_unknown_or_effectful_tool(name):
    ctx, _, _ = run(
        [reply(call(i, name=name)) for i in range(12)],
        [failed("retryable") for _ in range(12)],
        allowed={name},
    )
    assert len(ctx.commands) == 1


def test_successful_duplicate_still_closes_without_dispatch():
    ctx, rt, out = run([reply(call(1)), reply(call(2)), reply()], [result()])
    assert len(ctx.commands) == 1
    assert out.state.total_tool_calls == 1
    assert rt.calls[-1]["tool_choice"] == "none"


def test_retry_then_success_then_duplicate_does_not_dispatch_third_time():
    ctx, _, out = run(
        [reply(call(1)), reply(call(2)), reply(call(3)), reply()],
        [failed("retryable"), result()],
    )
    assert len(ctx.commands) == 2
    assert out.state.total_tool_calls == 2


def test_distinct_arguments_can_dispatch():
    ctx, _, _ = run(
        [reply(call(1)), reply(call(2, path="b")), reply()],
        [failed("retryable"), result()],
    )
    assert [c.args["path"] for c in ctx.commands] == ["a", "b"]


@pytest.mark.parametrize("companion", ["file.read", "file.write"])
def test_mixed_success_and_failure_batch_is_not_blindly_replayed(companion):
    ctx, _, _ = run(
        [
            reply(call(1), call(2, name=companion, path="b")),
            reply(call(3), call(4, name=companion, path="b")),
            reply(),
        ],
        [failed("retryable"), result()],
    )
    assert len(ctx.commands) == 2


def test_all_failed_safe_reads_can_retry_as_batch():
    ctx, _, out = run(
        [reply(call(1), call(2, path="b")), reply(call(3), call(4, path="b")), reply()],
        [failed("retryable"), failed("timeout_code"), result(), result()],
    )
    assert len(ctx.commands) == out.state.total_tool_calls == 4
    assert ctx.state.budgets_remaining.tool_calls == 16


@pytest.mark.parametrize("budget", ["tools", "tokens", "llm", "profile"])
def test_redispatch_cannot_exceed_existing_budgets(budget):
    state = _state(
        tool_calls=1 if budget == "tools" else 20,
        tokens=5 if budget == "tokens" else 2000,
        llm_calls_max=1 if budget == "llm" else 20,
    )
    ctx, _, out = run(
        [reply(call(1)), reply(call(2)), reply()],
        [failed("retryable"), result()],
        state=state,
        max_tool_calls_per_loop=1 if budget == "profile" else None,
    )
    assert len(ctx.commands) <= 1
    assert ctx.state.budgets_remaining.tool_calls >= 0
    assert out.termination_reason in {
        "budget_exhausted",
        "iteration_cap",
        "finalization_incomplete",
        "final_text",
    }


def test_recovery_disabled_stops_on_first_failure():
    ctx, _, out = run(
        [reply(call(1)), reply(call(2)), reply()],
        [failed("retryable"), result()],
        allow_llm_recovery_after_tool_failure=False,
    )
    assert len(ctx.commands) == 1
    assert out.termination_reason == "tool_failure_no_recovery"


def test_same_read_in_changed_batch_is_not_served_cached_failure():
    ctx, _, out = run(
        [reply(call(1)), reply(call(2), call(3, path="b")), reply()],
        [failed("retryable"), result(), result()],
    )
    assert [c.args["path"] for c in ctx.commands] == ["a", "a", "b"]
    assert out.state.scratchpad["loop.cache_hits"] == 0


def test_successful_read_in_changed_batch_keeps_cache():
    ctx, _, out = run(
        [reply(call(1)), reply(call(2), call(3, path="b")), reply()],
        [result(), result()],
    )
    assert [c.args["path"] for c in ctx.commands] == ["a", "b"]
    assert out.state.scratchpad["loop.cache_hits"] == 1


@pytest.mark.parametrize("phase", ["available", "spent", "legacy"])
def test_coding_checkpoint_preserves_read_retry_state(tmp_path, phase):
    from openminion.modules.brain.loop.strategies.coding import CodingProfileRunner

    calls = [reply(call(1)), reply()]
    outcomes = [failed("retryable")]
    if phase == "spent":
        calls.insert(1, reply(call(2)))
        outcomes.append(failed("retryable"))
    _, _, first = run(calls, outcomes)
    owner = CodingProfileRunner()
    owner._loop_state = first.state
    payload = owner.snapshot_state()
    if phase == "legacy":
        for facts in payload["scratchpad"][
            "duplicate_signature_execution_facts"
        ].values():
            facts.pop("all_retryable_reads", None)
    saved = tmp_path / "checkpoint.json"
    saved.write_text(json.dumps(payload))
    restored = CodingProfileRunner()
    restored.restore_state(json.loads(saved.read_text()))
    ctx, _, out = run(
        [reply(call(i)) for i in range(3, 15)],
        [failed("retryable")],
        initial=restored._loop_state,
    )
    assert len(ctx.commands) == (1 if phase == "available" else 0)
    assert out.state.total_tool_calls == (1 if phase == "legacy" else 2)


@pytest.mark.parametrize("session", ["parent", "child", "unrelated"])
def test_independent_loop_states_do_not_share_allowance(session):
    state = _state(tool_calls=20, llm_calls_max=20)
    state.session_id = session
    ctx, _, _ = run(
        [reply(call(1)), reply(call(2)), reply()],
        [failed("retryable"), result()],
        state=state,
    )
    assert len(ctx.commands) == 2


def test_retry_is_rechecked_by_normal_execution_after_permission_changes():
    ctx, _, out = run(
        [reply(call(1)), reply(call(2)), reply(call(3)), reply()],
        [failed("retryable"), result("blocked", code="POLICY_DENIED")],
    )
    assert len(ctx.commands) == 2
    assert out.state.total_tool_calls == 2
    assert ctx.outcomes[1].action_result.status == "blocked"
    assert ctx.state.budgets_remaining.tool_calls == 18


def test_spent_retry_survives_successful_unrelated_mutation():
    ctx, _, out = run(
        [
            reply(call(1)),
            reply(call(2)),
            reply(call(3, name="file.write", path="b", content="x")),
            reply(call(4)),
            reply(call(5)),
            reply(call(6)),
            reply(),
        ],
        [failed("retryable"), failed("retryable"), result(), failed("retryable")],
    )
    # Existing workspace invalidation permits a new read; it must not renew redispatch.
    assert [c.tool_name for c in ctx.commands] == [
        "file.read",
        "file.read",
        "file.write",
        "file.read",
    ]
    assert list(out.state.scratchpad["duplicate_read_redispatch_counts"].values()) == [
        1
    ]


@pytest.mark.parametrize("same_content", [True, False])
def test_corrective_file_write_boundary_uses_actual_file_state(tmp_path, same_content):
    path = tmp_path / "report"
    path.write_text("OLD")

    class FileContext(_LoopContext):
        writes = 0

        def execute_command(self, *, command, include_reflect=False):
            self.commands.append(command)
            if command.tool_name == "file.write":
                self.writes += 1
                if not same_content or self.writes > 1:
                    path.write_text(command.args["content"])
                return result(
                    outputs={
                        "path": "report",
                        "bytes_written": len(command.args["content"]),
                    }
                )
            return result(outputs={"content": path.read_text()})

    ctx = FileContext(state=_state(tool_calls=20, llm_calls_max=20))
    rt = _FakeRuntime(
        responses=[
            reply(
                call(
                    1,
                    name="file.write",
                    path="report",
                    content="READY" if same_content else "OLD",
                )
            ),
            reply(call(2, path="report")),
            reply(call(3, name="file.write", path="report", content="READY")),
            reply(call(4, path="report")),
            reply(),
        ]
    )
    out = run_adaptive_tool_loop(
        ctx,
        profile=_profile(
            max_iterations=10, allowed_tools=frozenset({"file.read", "file.write"})
        ),
        runtime=rt,
        model="fixture",
        initial_messages=[Message(role="user", content="Write READY and verify.")],
        tool_specs=_tool_specs("file.read", "file.write"),
    )
    assert len(ctx.commands) == 4
    assert path.read_text() == "READY"
    assert out.state.total_tool_calls == len(ctx.commands)


def test_ambiguous_effect_is_not_replayed_after_timeout():
    effects = []

    class EffectContext(_LoopContext):
        def execute_command(self, *, command, include_reflect=False):
            self.commands.append(command)
            effects.append(command.args)
            return failed("timeout_code")

    ctx = EffectContext(state=_state(tool_calls=20, llm_calls_max=20))
    rt = _FakeRuntime(
        responses=[reply(call(i, name="payments.send")) for i in range(12)]
    )
    out = run_adaptive_tool_loop(
        ctx,
        profile=_profile(max_iterations=10, allowed_tools=frozenset({"payments.send"})),
        runtime=rt,
        model="fixture",
        initial_messages=[Message(role="user", content="Send once.")],
        tool_specs=_tool_specs("payments.send"),
    )
    assert len(effects) == len(ctx.commands) == out.state.total_tool_calls == 1


@pytest.mark.parametrize("restart", [False, True])
def test_saved_action_receipt_is_reused_with_same_key(tmp_path, restart):
    from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
    from tests.brain.test_mission_mode import _build_runner

    effects = []

    class Tool:
        contract_version = "v1"

        def list_tools(self):
            return [{"name": "echo"}]

        def execute(self, *, command, session_id, trace_id):
            effects.append(command["args"])
            return {
                "status": "success",
                "summary": "receipt",
                "outputs": {"receipt": 731},
            }

    runner, session = _build_runner(tmp_path)
    runner.tool_api = Tool()
    state = runner._load_or_init_state("receipt")
    state.budgets_remaining.tool_calls = 10
    logger = CanonicalEventLogger(
        session_api=session,
        session_id=state.session_id,
        agent_id=runner.profile.agent_id,
    )
    command = ToolCommand(
        tool_name="echo", title="Effect", args={"value": 731}, idempotency_key="stable"
    )
    first, _ = runner._act(state=state, command=command, logger=logger)
    runner._save_state(state)
    if restart:
        runner, session = _build_runner(tmp_path)
        runner.tool_api = Tool()
        state = runner._load_or_init_state("receipt")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id=state.session_id,
            agent_id=runner.profile.agent_id,
        )
    second, _ = runner._act(
        state=state,
        command=command.model_copy(update={"command_id": "fresh"}),
        logger=logger,
    )
    assert len(effects) == 1
    assert second.outputs == first.outputs == {"receipt": 731}
    assert "stable" in state.idempotency_cache


def test_real_executor_rechecks_policy_before_retry_dispatch(tmp_path):
    from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
    from openminion.modules.brain.schemas import PolicyDecision
    from openminion.modules.brain.tools.executor.command_executor import (
        RunnerCommandExecutor,
    )
    from tests.brain.test_mission_mode import _build_runner

    effects, approvals = [], []

    class Policy:
        def evaluate(self, *, command, working_state, session_context):
            approvals.append(command.command_id)
            return PolicyDecision(
                outcome="ALLOW" if len(approvals) == 1 else "DENY",
                explanation="fixture",
            )

    class Tool:
        contract_version = "v1"

        def list_tools(self):
            return [{"name": "file.read"}]

        def execute(self, *, command, session_id, trace_id):
            effects.append(command)
            return {
                "status": "failed",
                "summary": "timeout",
                "error": {"code": "TIMEOUT", "message": "timeout"},
            }

    runner, session = _build_runner(tmp_path)
    runner.policy_api, runner.tool_api = Policy(), Tool()
    state = runner._load_or_init_state("policy-retry")
    state.budgets_remaining.tool_calls = 10
    state.llm_calls_max = 20
    executor = RunnerCommandExecutor(runner)
    logger = CanonicalEventLogger(
        session_api=session,
        session_id=state.session_id,
        agent_id=runner.profile.agent_id,
    )

    class Context(_LoopContext):
        def execute_command(self, *, command, include_reflect=False):
            self.commands.append(command)
            return executor.execute_command(
                state=self.state, command=command, logger=logger, include_reflect=False
            )

    ctx = Context(state=state)
    out = run_adaptive_tool_loop(
        ctx,
        profile=_profile(max_iterations=10, allowed_tools=frozenset({"file.read"})),
        runtime=_FakeRuntime(responses=[reply(call(1)), reply(call(2)), reply()]),
        model="fixture",
        initial_messages=[Message(role="user", content="Read a")],
        tool_specs=_tool_specs("file.read"),
    )
    assert approvals == ["call-1", "call-2"]
    assert len(effects) == 1
    assert out.termination_reason == "needs_user"
    assert state.pending_confirmation_command is None
