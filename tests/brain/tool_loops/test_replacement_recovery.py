"""Guarded replacement dispatch, conflict and persisted allowance regressions."""

import json

import pytest

from openminion.modules.brain.loop.tools import run_adaptive_tool_loop
from openminion.modules.brain.loop.strategies.coding import CodingProfileRunner
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import Message
from openminion.tools.file.plugin import _get_backend, _h_read_file, _h_write_file
from tests.test_file_tool_characterization import _ctx
from tests.brain.tool_loops.test_engine import (
    _LoopContext,
    _profile,
    _state,
    _tool_specs,
    _FakeRuntime,
)
from tests.brain.tool_loops.test_duplicate_read_recovery import call, reply


class NativeContext(_LoopContext):
    def __init__(self, tmp_path, mode="dropped", backend="local"):
        super().__init__(state=_state(tool_calls=20, tokens=2000, llm_calls_max=20))
        self.native = _ctx(tmp_path)
        self.target = self.native.workspace / "report"
        self.target.write_text("OLD")
        self.native.policy.raw["file_backend"] = backend
        if backend == "memory":
            assert _h_write_file({"path": "report", "content": "OLD"}, self.native)[
                "ok"
            ]
        self.mode = mode
        self.writes = 0
        self.results = []

    def execute_command(self, *, command, include_reflect=False):
        self.commands.append(command)
        if command.tool_name == "file.write":
            self.writes += 1
            if (
                self.writes == 1 and self.mode != "normal"
            ) or self.mode == "always_dropped":
                raw = {"ok": True, "path": str(self.target), "bytes_written": 5}
            elif self.mode == "denied":
                raw = {
                    "ok": False,
                    "error": {"code": "POLICY_DENIED", "message": "revoked"},
                }
            else:
                if self.mode == "race":
                    self.target.write_text("SOMEONE ELSE")
                raw = _h_write_file(command.args, self.native)
        else:
            raw = _h_read_file(command.args, self.native)
        result = ActionResult(
            command_id=command.command_id,
            status="success" if raw["ok"] else "failed",
            summary="native fixture",
            outputs={k: v for k, v in raw.items() if k != "error"},
            error=raw.get("error"),
        )
        self.results.append(result)
        return CommandExecutionOutcome(approved_command=command, action_result=result)


def execute(ctx, responses, initial=None):
    return run_adaptive_tool_loop(
        ctx,
        profile=_profile(
            max_iterations=12, allowed_tools=frozenset({"file.read", "file.write"})
        ),
        runtime=_FakeRuntime(responses=responses),
        model="fixture",
        initial_messages=[Message(role="user", content="Write READY and verify.")],
        initial_state=initial,
        tool_specs=_tool_specs("file.read", "file.write"),
    )


def sequence():
    return [
        reply(call(1, name="file.write", path="report", content="READY")),
        reply(call(2, path="report")),
        reply(call(3, name="file.write", path="report", content="READY")),
        reply(call(4, path="report")),
        reply(),
    ]


def test_native_corrective_write_is_guarded_and_verified(tmp_path):
    ctx = NativeContext(tmp_path)
    out = execute(ctx, sequence())
    assert len(ctx.commands) == out.state.total_tool_calls == 4
    assert ctx.target.read_text() == "READY"
    assert ctx.results[-1].outputs["content"] == "READY"
    assert ctx.commands[0].args == {"path": "report", "content": "READY"}
    assert ctx.commands[2].args["expected_content_sha256"]
    assert ctx.commands[2].args["expected_version"] == ctx.results[1].outputs["version"]
    assert ctx.state.budgets_remaining.tool_calls == 16


@pytest.mark.parametrize(
    "mode,expected,code",
    [
        ("race", "SOMEONE ELSE", "PRECONDITION_FAILED"),
        ("denied", "OLD", "POLICY_DENIED"),
    ],
)
def test_changed_or_denied_target_is_not_overwritten(tmp_path, mode, expected, code):
    ctx = NativeContext(tmp_path, mode)
    execute(ctx, sequence())
    assert ctx.target.read_text() == expected
    assert ctx.results[2].error.code == code
    assert ctx.writes == 2


def test_second_contradiction_does_not_renew_correction(tmp_path):
    ctx = NativeContext(tmp_path, "always_dropped")
    responses = sequence()[:-1] + [
        reply(call(5, name="file.write", path="report", content="READY")),
        reply(),
    ]
    out = execute(ctx, responses)
    assert ctx.writes == 2
    assert ctx.target.read_text() == "OLD"
    assert all(
        entry["used"]
        for entry in out.state.scratchpad["file_replacement_recovery"].values()
    )


@pytest.mark.parametrize("read_args", [{"max_chars": 1}, {"offset": 1}])
def test_partial_read_cannot_authorize_correction(tmp_path, read_args):
    ctx = NativeContext(tmp_path)
    responses = sequence()
    responses[1] = reply(call(2, path="report", **read_args))
    execute(ctx, responses)
    assert ctx.writes == 1
    assert ctx.target.read_text() == "OLD"


def test_append_is_never_correctively_replayed(tmp_path):
    ctx = NativeContext(tmp_path)
    responses = sequence()
    responses[0].tool_calls[0].arguments["append"] = True
    responses[2].tool_calls[0].arguments["append"] = True
    execute(ctx, responses)
    assert ctx.writes == 1


@pytest.mark.parametrize("spent", [False, True])
def test_correction_allowance_survives_coding_checkpoint(tmp_path, spent):
    ctx = NativeContext(tmp_path, "always_dropped" if spent else "dropped")
    first = execute(ctx, sequence()[: 4 if spent else 2] + [reply()])
    owner = CodingProfileRunner()
    owner._loop_state = first.state
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(owner.snapshot_state()))
    restored = CodingProfileRunner()
    restored.restore_state(json.loads(checkpoint.read_text()))
    execute(
        ctx,
        [
            reply(call(i, name="file.write", path="report", content="READY"))
            for i in range(5, 9)
        ]
        + [reply()],
        restored._loop_state,
    )
    assert ctx.writes == 2
    assert ctx.target.read_text() == ("OLD" if spent else "READY")


def test_successful_readback_keeps_duplicate_write_suppressed(tmp_path):
    ctx = NativeContext(tmp_path)
    ctx.target.write_text("READY")
    execute(ctx, sequence())
    assert ctx.writes == 1
    assert ctx.target.read_text() == "READY"


def test_repeating_decorated_correction_does_not_get_another_dispatch(tmp_path):
    ctx = NativeContext(tmp_path, "always_dropped")
    first = execute(ctx, sequence())
    repeated = call(5, name="file.write")
    repeated.arguments = dict(ctx.commands[2].args)
    execute(
        ctx,
        [reply(repeated), reply(repeated.model_copy(update={"id": "fresh"})), reply()],
        first.state,
    )
    assert ctx.writes == 2
    assert ctx.target.read_text() == "OLD"


def test_other_path_readback_does_not_authorize_correction(tmp_path):
    ctx = NativeContext(tmp_path)
    (ctx.native.workspace / "other").write_text("OLD")
    responses = sequence()
    responses[1] = reply(call(2, path="other"))
    execute(ctx, responses)
    assert ctx.writes == 1


def test_exhausted_tool_budget_prevents_corrective_dispatch(tmp_path):
    ctx = NativeContext(tmp_path)
    ctx.state.budgets_remaining.tool_calls = 2
    execute(ctx, sequence())
    assert ctx.writes == 1
    assert ctx.target.read_text() == "OLD"


def test_real_executor_reauthorizes_corrective_write(tmp_path):
    from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
    from openminion.modules.brain.schemas import PolicyDecision
    from openminion.modules.brain.tools.executor.command_executor import (
        RunnerCommandExecutor,
    )
    from tests.brain.test_mission_mode import _build_runner

    native = NativeContext(tmp_path)
    approvals = []

    class Policy:
        def evaluate(self, *, command, working_state, session_context):
            approvals.append(command.tool_name)
            deny = (
                command.tool_name == "file.write" and approvals.count("file.write") == 2
            )
            return PolicyDecision(
                outcome="DENY" if deny else "ALLOW", explanation="fixture"
            )

    class Tool:
        contract_version = "v1"

        def list_tools(self):
            return [{"name": "file.read"}, {"name": "file.write"}]

        def execute(self, *, command, session_id, trace_id):
            from openminion.modules.brain.schemas import ToolCommand

            outcome = native.execute_command(
                command=ToolCommand(
                    tool_name=command["tool_name"],
                    title="Fixture",
                    args=command["args"],
                    command_id=command["command_id"],
                )
            )
            return outcome.action_result.model_dump(mode="json")

    runner, session = _build_runner(tmp_path / "runner")
    runner.policy_api, runner.tool_api = Policy(), Tool()
    state = runner._load_or_init_state("write-recovery")
    state.budgets_remaining.tool_calls = 20
    state.llm_calls_max = 20
    logger = CanonicalEventLogger(
        session_api=session,
        session_id=state.session_id,
        agent_id=runner.profile.agent_id,
    )
    executor = RunnerCommandExecutor(runner)

    class Context(_LoopContext):
        def execute_command(self, *, command, include_reflect=False):
            return executor.execute_command(
                state=self.state, command=command, logger=logger, include_reflect=False
            )

    out = execute(Context(state=state), sequence())
    assert approvals == ["file.write", "file.read", "file.write"]
    assert native.writes == 1
    assert native.target.read_text() == "OLD"
    assert out.termination_reason == "needs_user"


def test_nullable_provider_guard_fields_do_not_disable_recovery(tmp_path):
    ctx = NativeContext(tmp_path)
    responses = sequence()
    for index in (0, 2):
        responses[index].tool_calls[0].arguments.update(
            expected_content_sha256=None, expected_version=None
        )
    execute(ctx, responses)
    assert ctx.writes == 2
    assert ctx.target.read_text() == "READY"


@pytest.mark.parametrize("guard", ["expected_content_sha256", "expected_version"])
def test_explicit_existing_precondition_is_not_replaced_by_recovery(tmp_path, guard):
    ctx = NativeContext(tmp_path)
    observed = _h_read_file({"path": "report"}, ctx.native)
    value = observed["version"] if guard == "expected_version" else "0" * 64
    responses = sequence()
    for index in (0, 2):
        responses[index].tool_calls[0].arguments[guard] = value
    execute(ctx, responses)
    assert ctx.writes == 1
    assert ctx.target.read_text() == "OLD"


@pytest.mark.parametrize("backend", ["local", "memory"])
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
@pytest.mark.parametrize("mode", ["normal", "dropped", "always_dropped"])
def test_newline_readback_preserves_bytes_and_bounded_writes(
    tmp_path, backend, newline, mode
):
    ctx = NativeContext(tmp_path, mode, backend)
    desired = f"first{newline}second{newline}"
    responses = sequence()
    for index in (0, 2):
        responses[index].tool_calls[0].arguments["content"] = desired
    out = execute(ctx, responses)
    expected_writes = 1 if mode == "normal" else 2
    assert ctx.writes == expected_writes
    assert len(ctx.commands) == out.state.total_tool_calls == 2 * expected_writes
    expected = "OLD" if mode == "always_dropped" else desired
    actual = (
        ctx.target.read_bytes()
        if backend == "local"
        else _get_backend(ctx.native).read(str(ctx.target)).content.encode("utf-8")
    )
    assert actual == expected.encode("utf-8")
    assert ctx.commands[0].args["content"] == desired
    if mode == "normal":
        expected_read = (
            desired.replace("\r\n", "\n").replace("\r", "\n")
            if backend == "local"
            else desired
        )
        assert ctx.results[1].outputs["content"] == expected_read
    elif mode == "dropped":
        assert ctx.commands[2].args["content"] == desired
        assert ctx.commands[2].args["expected_content_sha256"]


@pytest.mark.parametrize("backend", ["local", "memory"])
@pytest.mark.parametrize("observed", ["a\r\nb", "a\rb"])
def test_distinct_newline_bytes_still_authorize_real_correction(
    tmp_path, backend, observed
):
    ctx = NativeContext(tmp_path, backend=backend)
    assert _h_write_file({"path": "report", "content": observed}, ctx.native)["ok"]
    responses = sequence()
    for index in (0, 2):
        responses[index].tool_calls[0].arguments["content"] = "a\nb"
    execute(ctx, responses)
    assert ctx.writes == 2
    actual = (
        ctx.target.read_bytes()
        if backend == "local"
        else _get_backend(ctx.native).read(str(ctx.target)).content.encode("utf-8")
    )
    assert actual == b"a\nb"


def test_replacement_evidence_is_compact_and_checkpointed(tmp_path):
    ctx = NativeContext(tmp_path)
    responses = sequence()[:2] + [reply()]
    responses[0].tool_calls[0].arguments["content"] = "large payload" * 3000
    out = execute(ctx, responses)
    owner = CodingProfileRunner()
    owner._loop_state = out.state
    restored = CodingProfileRunner()
    restored.restore_state(json.loads(json.dumps(owner.snapshot_state())))
    history = restored._loop_state.scratchpad["file_replacement_recovery"]
    assert len(history) == 1
    assert len(next(iter(history))) == 64
    assert "content" not in next(iter(history.values()))
    assert len(json.dumps(history)) < 1024


def test_unrelated_read_does_not_create_replacement_history(tmp_path):
    ctx = NativeContext(tmp_path)
    out = execute(ctx, [reply(call(1, path="report")), reply()])
    assert "file_replacement_recovery" not in out.state.scratchpad
