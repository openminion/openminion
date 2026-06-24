from openminion.services.runtime.engine import (
    PolicyDecision,
    RuntimeContext,
    RuntimeEngine,
    ToolCall,
)
from openminion.modules.task.runtime.service import InMemoryTaskCtl
from openminion.base.runtime.interfaces import RUNTIME_INTERFACE_VERSION
from openminion.base.runtime.sandbox import (
    ExecResult,
    ExecSpec,
    ExecutionSandboxSpec,
    FsResult,
    FsWriteSpec,
)


# Helpers / fakes


def _ctx(workspace="/ws") -> RuntimeContext:
    return RuntimeContext(
        trace_id="tr-1",
        agent_id="agent-1",
        session_id="sess-1",
        run_id="run-1",
        workspace_root=workspace,
    )


class _AlwaysAllow:
    contract_version = RUNTIME_INTERFACE_VERSION

    def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision:
        return PolicyDecision(outcome="allow", policy_request_id="pr-1")


class _AlwaysDeny:
    contract_version = RUNTIME_INTERFACE_VERSION

    def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision:
        return PolicyDecision(outcome="deny", policy_request_id="pr-deny")


class _AlwaysConfirm:
    contract_version = RUNTIME_INTERFACE_VERSION

    def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision:
        return PolicyDecision(outcome="confirm", policy_request_id="pr-confirm")


class _AllowWithConstraints:
    contract_version = RUNTIME_INTERFACE_VERSION

    def __init__(self, constraints: dict):
        self._constraints = constraints

    def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision:
        return PolicyDecision(
            outcome="allow_with_constraints",
            policy_request_id="pr-constrained",
            constraints=self._constraints,
        )


class _StubRunner:
    name = "stub"
    contract_version = RUNTIME_INTERFACE_VERSION

    def __init__(self, *, exec_result=None, fs_result=None, net_result=None):
        self._exec = exec_result or ExecResult(returncode=0, stdout="ok", stderr="")
        self._fs = fs_result or FsResult(success=True, path="/ws/file.txt")
        self._net = None

    def run_exec(self, spec, sandbox):
        return self._exec

    def fs_write(self, spec, sandbox):
        return self._fs

    def fs_delete(self, spec, sandbox):
        return self._fs

    def net_fetch(self, spec, sandbox):
        if self._net is None:
            raise PermissionError("net denied in stub")
        return self._net


def _make_exec_call(idempotency_key=None) -> ToolCall:
    return ToolCall(
        tool_call_id="tc-1",
        name="exec",
        kind="exec",
        spec=ExecSpec(cmd=["echo", "hi"]),
        idempotency_key=idempotency_key,
    )


def _make_fswrite_call() -> ToolCall:
    return ToolCall(
        tool_call_id="tc-2",
        name="fs_write",
        kind="fs.write",
        spec=FsWriteSpec(path="/ws/file.txt", content="hello"),
    )


# Policy handshake event tests


def test_allow_emits_required_events():
    events: list[tuple] = []
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysAllow(),
        on_event=lambda name, payload: events.append((name, payload)),
    )
    engine.execute_tool_call(_make_exec_call(), _ctx())

    names = [e[0] for e in events]
    assert "tool.call.requested" in names
    assert "policy.requested" in names
    assert "policy.decision.created" in names
    assert "runtime.exec.started" in names
    assert "runtime.exec.completed" in names
    assert "tool.call.completed" in names


def test_deny_emits_violation_and_blocked():
    events: list[tuple] = []
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysDeny(),
        on_event=lambda name, payload: events.append((name, payload)),
    )
    result = engine.execute_tool_call(_make_exec_call(), _ctx())

    assert result.outcome == "blocked"
    assert result.error == "policy_deny"
    names = [e[0] for e in events]
    assert "runtime.violation" in names
    assert "tool.call.blocked" in names
    assert "runtime.exec.started" not in names


def test_confirm_approved_executes():
    events: list[tuple] = []
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysConfirm(),
        on_event=lambda name, payload: events.append((name, payload)),
        on_confirm=lambda tc, ctx, dec: True,  # approve
    )
    result = engine.execute_tool_call(_make_exec_call(), _ctx())

    assert result.outcome == "completed"
    names = [e[0] for e in events]
    assert "runtime.exec.started" in names
    assert "tool.call.completed" in names


def test_confirm_denied_blocks():
    events: list[tuple] = []
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysConfirm(),
        on_event=lambda name, payload: events.append((name, payload)),
        on_confirm=lambda tc, ctx, dec: False,  # deny
    )
    result = engine.execute_tool_call(_make_exec_call(), _ctx())

    assert result.outcome == "blocked"
    assert result.error == "confirm_denied"
    names = [e[0] for e in events]
    assert "runtime.violation" in names
    assert "tool.call.blocked" in names


def test_confirm_without_handler_blocks():
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysConfirm(),
        on_confirm=None,
    )
    result = engine.execute_tool_call(_make_exec_call(), _ctx())
    assert result.outcome == "blocked"


def test_confirm_without_handler_records_pending_resume_pointer_when_cursor_present():
    task_ctl = InMemoryTaskCtl()
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysConfirm(),
        on_confirm=None,
        task_ctl=task_ctl,
    )
    ctx = RuntimeContext(
        trace_id="tr-1",
        agent_id="agent-1",
        session_id="sess-1",
        run_id="run-1",
        workspace_root="/ws",
        task_id="task-1",
        plan_id="plan-1",
        step_id="step-1",
        attempt=2,
        turn_id="turn-1",
        pack_id="pack-1",
    )

    result = engine.execute_tool_call(_make_exec_call(), ctx)

    assert result.outcome == "blocked"
    resumed = task_ctl.resume_pending_action(
        policy_request_id="pr-confirm",
        decision_id="decision-1",
        trace_id="tr-1",
    )
    assert resumed.task_id == "task-1"
    assert resumed.plan_id == "plan-1"
    assert resumed.step_id == "step-1"
    assert resumed.attempt == 2
    assert resumed.turn_id == "turn-1"
    assert resumed.pack_id == "pack-1"
    assert [event["type"] for event in task_ctl.list_events()] == [
        "mission.paused",
        "mission.resumed",
    ]


def test_allow_with_constraints_passes_constraints_to_sandbox():
    captured_sandbox: list[ExecutionSandboxSpec] = []

    class _CapturingRunner:
        name = "capturing"
        contract_version = RUNTIME_INTERFACE_VERSION

        def run_exec(self, spec, sandbox):
            captured_sandbox.append(sandbox)
            return ExecResult(returncode=0, stdout="", stderr="")

        def fs_write(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def fs_delete(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def net_fetch(self, spec, sandbox):
            raise PermissionError("no net")

    engine = RuntimeEngine(
        runner=_CapturingRunner(),
        policy=_AllowWithConstraints({"cmd_allowlist": ["echo"]}),
    )
    ctx = RuntimeContext(
        trace_id="tr-1",
        agent_id="a",
        session_id="s",
        run_id="r",
        workspace_root="/ws",
        tool_caps={"cmd_allowlist": ["echo", "ls"]},
    )
    engine.execute_tool_call(_make_exec_call(), ctx)

    assert len(captured_sandbox) == 1
    sb = captured_sandbox[0]
    # policy narrowed ["echo", "ls"] → ["echo"]
    assert sb.cmd_allowlist == ["echo"]
    assert "ls" not in sb.cmd_allowlist


# Idempotency tests (SPEC-I01)


def test_idempotency_key_prevents_second_execution():
    call_count = [0]

    class _CountingRunner:
        name = "counting"
        contract_version = RUNTIME_INTERFACE_VERSION

        def run_exec(self, spec, sandbox):
            call_count[0] += 1
            return ExecResult(returncode=0, stdout="counted", stderr="")

        def fs_write(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def fs_delete(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def net_fetch(self, spec, sandbox):
            raise PermissionError("no net")

    engine = RuntimeEngine(runner=_CountingRunner(), policy=_AlwaysAllow())
    call = _make_exec_call(idempotency_key="idem-key-1")
    ctx = _ctx()

    r1 = engine.execute_tool_call(call, ctx)
    r2 = engine.execute_tool_call(call, ctx)

    assert call_count[0] == 1  # executed only once
    assert r1.outcome == "completed"
    assert r2.outcome == "cached"
    assert r2.from_cache is True
    assert r2.result.stdout == "counted"


def test_idempotency_cache_not_populated_on_block():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AlwaysDeny())
    call = _make_exec_call(idempotency_key="idem-blocked")
    r1 = engine.execute_tool_call(call, _ctx())
    r2 = engine.execute_tool_call(call, _ctx())

    # Both should be blocked (not cached)
    assert r1.outcome == "blocked"
    assert r2.outcome == "blocked"
    assert not r2.from_cache


def test_no_idempotency_key_executes_every_time():
    call_count = [0]

    class _CountingRunner:
        name = "counting"
        contract_version = RUNTIME_INTERFACE_VERSION

        def run_exec(self, spec, sandbox):
            call_count[0] += 1
            return ExecResult(returncode=0, stdout="", stderr="")

        def fs_write(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def fs_delete(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def net_fetch(self, spec, sandbox):
            raise PermissionError("no net")

    engine = RuntimeEngine(runner=_CountingRunner(), policy=_AlwaysAllow())
    call = _make_exec_call(idempotency_key=None)
    engine.execute_tool_call(call, _ctx())
    engine.execute_tool_call(call, _ctx())
    assert call_count[0] == 2


# Audit event content tests


def test_all_events_have_ts_field():
    events: list[dict] = []
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysAllow(),
        on_event=lambda name, payload: events.append(payload),
    )
    engine.execute_tool_call(_make_exec_call(), _ctx())
    assert len(events) > 0
    for payload in events:
        assert "ts" in payload, f"Missing 'ts' in event payload: {payload}"


def test_all_events_have_correlation_fields():
    events: list[tuple] = []
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysAllow(),
        on_event=lambda name, payload: events.append((name, payload)),
    )
    engine.execute_tool_call(_make_exec_call(), _ctx())

    # Events that carry correlation fields
    correlated_events = {
        "tool.call.requested",
        "policy.requested",
        "policy.decision.created",
        "runtime.exec.started",
        "runtime.exec.completed",
        "tool.call.completed",
    }
    for name, payload in events:
        if name in correlated_events:
            assert "tool_call_id" in payload, f"{name} missing tool_call_id"
            assert "trace_id" in payload, f"{name} missing trace_id"
            assert "agent_id" in payload, f"{name} missing agent_id"
            assert "session_id" in payload, f"{name} missing session_id"
            assert "run_id" in payload, f"{name} missing run_id"


def test_fs_write_emits_runtime_fs_write_event():
    events: list[tuple] = []
    engine = RuntimeEngine(
        runner=_StubRunner(),
        policy=_AlwaysAllow(),
        on_event=lambda name, payload: events.append((name, payload)),
    )
    engine.execute_tool_call(_make_fswrite_call(), _ctx())
    names = [e[0] for e in events]
    assert "runtime.fs.write" in names


def test_unknown_kind_returns_error():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AlwaysAllow())
    call = ToolCall(
        tool_call_id="tc-bad",
        name="mystery",
        kind="mystery.action",
        spec=ExecSpec(cmd=["echo"]),
    )
    result = engine.execute_tool_call(call, _ctx())
    assert result.outcome == "error"
    assert "unknown_kind" in (result.error or "")
