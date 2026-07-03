from openminion.services.runtime.engine import (
    PolicyDecision,
    RuntimeContext,
    RuntimeEngine,
    ToolCall,
)
from openminion.base.runtime.interfaces import RUNTIME_INTERFACE_VERSION
from openminion.base.runtime.runners import LocalRunner
from openminion.base.runtime.sandbox import (
    ExecResult,
    ExecSpec,
    FsDeleteSpec,
    FsResult,
    FsWriteSpec,
    NetFetchSpec,
    NetResult,
)


class _AllowPolicy:
    contract_version = RUNTIME_INTERFACE_VERSION

    def evaluate(self, tool_call, ctx) -> PolicyDecision:
        return PolicyDecision(outcome="allow", policy_request_id="pr-allow")


class _DenyPolicy:
    contract_version = RUNTIME_INTERFACE_VERSION

    def evaluate(self, tool_call, ctx) -> PolicyDecision:
        return PolicyDecision(outcome="deny", policy_request_id="pr-deny")


class _StubRunner:
    name = "stub"
    contract_version = RUNTIME_INTERFACE_VERSION

    def run_exec(self, spec, sandbox):
        return ExecResult(returncode=0, stdout="ok", stderr="")

    def fs_write(self, spec, sandbox):
        return FsResult(success=True, path=spec.path)

    def fs_delete(self, spec, sandbox):
        return FsResult(success=True, path=spec.path)

    def net_fetch(self, spec, sandbox):
        return NetResult(status=200, body=b"hello")


def _ctx() -> RuntimeContext:
    return RuntimeContext(
        trace_id="tr-audit",
        agent_id="agent-audit",
        session_id="sess-audit",
        run_id="run-audit",
        workspace_root="/ws",
        tool_caps={"cmd_allowlist": ["echo"]},
    )


def _collect(engine: RuntimeEngine, call: ToolCall) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    engine._on_event = lambda name, payload: events.append((name, payload))  # type: ignore[method-assign]
    engine.execute_tool_call(call, _ctx())
    return events


def test_exec_full_event_sequence():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-exec",
        name="exec",
        kind="exec",
        spec=ExecSpec(cmd=["echo", "hi"]),
    )
    events = _collect(engine, call)
    names = [e[0] for e in events]
    assert names == [
        "tool.call.requested",
        "policy.requested",
        "policy.decision.created",
        "runtime.exec.started",
        "runtime.exec.completed",
        "tool.call.completed",
    ]


def test_exec_completed_has_returncode():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-rc", name="exec", kind="exec", spec=ExecSpec(cmd=["echo"])
    )
    events = _collect(engine, call)
    completed = next(p for n, p in events if n == "runtime.exec.completed")
    assert "returncode" in completed
    assert completed["returncode"] == 0


def test_fs_write_event_sequence():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-fw",
        name="fs_write",
        kind="fs.write",
        spec=FsWriteSpec(path="/ws/f.txt", content="x"),
    )
    events = _collect(engine, call)
    names = [e[0] for e in events]
    assert "runtime.fs.write" in names
    assert "tool.call.completed" in names
    assert "runtime.exec.started" not in names


def test_fs_write_event_has_path():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-fw2",
        name="fs_write",
        kind="fs.write",
        spec=FsWriteSpec(path="/ws/file.txt", content=""),
    )
    events = _collect(engine, call)
    fw_event = next(p for n, p in events if n == "runtime.fs.write")
    assert fw_event["path"] == "/ws/file.txt"


def test_fs_delete_event_sequence():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-fd",
        name="fs_delete",
        kind="fs.delete",
        spec=FsDeleteSpec(path="/ws/old.txt"),
    )
    events = _collect(engine, call)
    names = [e[0] for e in events]
    assert "runtime.fs.delete" in names
    assert "tool.call.completed" in names


def test_net_fetch_event_sequence():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-net",
        name="net_fetch",
        kind="net.fetch",
        spec=NetFetchSpec(url="https://example.com"),
    )
    events = _collect(engine, call)
    names = [e[0] for e in events]
    assert "runtime.net.requested" in names
    assert "tool.call.completed" in names


def test_net_requested_event_has_url():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-net2",
        name="net_fetch",
        kind="net.fetch",
        spec=NetFetchSpec(url="https://api.example.com/v1"),
    )
    events = _collect(engine, call)
    net_event = next(p for n, p in events if n == "runtime.net.requested")
    assert net_event["url"] == "https://api.example.com/v1"


def test_deny_emits_violation():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_DenyPolicy())
    call = ToolCall(
        tool_call_id="tc-deny", name="exec", kind="exec", spec=ExecSpec(cmd=["echo"])
    )
    events = _collect(engine, call)
    names = [e[0] for e in events]
    assert "runtime.violation" in names


def test_violation_has_reason():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_DenyPolicy())
    call = ToolCall(
        tool_call_id="tc-reason", name="exec", kind="exec", spec=ExecSpec(cmd=["echo"])
    )
    events = _collect(engine, call)
    violation = next(p for n, p in events if n == "runtime.violation")
    assert "reason" in violation
    assert violation["reason"] == "policy_deny"


def test_runner_enforcement_error_emits_violation(tmp_path):
    local = LocalRunner()
    engine = RuntimeEngine(runner=local, policy=_AllowPolicy())
    ctx = RuntimeContext(
        trace_id="tr-v",
        agent_id="a",
        session_id="s",
        run_id="r",
        workspace_root=str(tmp_path),
        tool_caps={"cmd_allowlist": []},  # empty → deny-all
    )
    call = ToolCall(
        tool_call_id="tc-block",
        name="exec",
        kind="exec",
        spec=ExecSpec(cmd=["echo", "hi"]),
    )
    events: list[tuple] = []
    engine._on_event = lambda name, payload: events.append((name, payload))  # type: ignore[method-assign]
    result = engine.execute_tool_call(call, ctx)
    names = [e[0] for e in events]
    assert result.outcome == "blocked"
    assert "runtime.violation" in names


def test_all_events_have_ts():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-ts", name="exec", kind="exec", spec=ExecSpec(cmd=["echo"])
    )
    events = _collect(engine, call)
    for name, payload in events:
        assert "ts" in payload, f"Missing 'ts' in event {name!r}: {payload}"


def test_all_events_have_tool_call_id():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="my-call-id", name="exec", kind="exec", spec=ExecSpec(cmd=["echo"])
    )
    events = _collect(engine, call)
    correlated = {
        "tool.call.requested",
        "policy.requested",
        "policy.decision.created",
        "runtime.exec.started",
        "runtime.exec.completed",
        "tool.call.completed",
    }
    for name, payload in events:
        if name in correlated:
            assert payload.get("tool_call_id") == "my-call-id", (
                f"{name} has wrong tool_call_id"
            )


def test_idempotency_replay_prevents_duplicate_exec():
    exec_count = [0]

    class _CountingRunner:
        name = "counting"
        contract_version = RUNTIME_INTERFACE_VERSION

        def run_exec(self, spec, sandbox):
            exec_count[0] += 1
            return ExecResult(returncode=0, stdout=f"count={exec_count[0]}", stderr="")

        def fs_write(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def fs_delete(self, spec, sandbox):
            return FsResult(success=True, path=spec.path)

        def net_fetch(self, spec, sandbox):
            return NetResult(status=200, body=b"")

    engine = RuntimeEngine(runner=_CountingRunner(), policy=_AllowPolicy())
    call = ToolCall(
        tool_call_id="tc-idem",
        name="exec",
        kind="exec",
        spec=ExecSpec(cmd=["echo"]),
        idempotency_key="replay-key-001",
    )
    r1 = engine.execute_tool_call(call, _ctx())
    r2 = engine.execute_tool_call(call, _ctx())
    r3 = engine.execute_tool_call(call, _ctx())

    assert exec_count[0] == 1
    assert r1.outcome == "completed"
    assert r2.outcome == "cached"
    assert r3.outcome == "cached"
    assert r2.from_cache is True


def test_idempotency_does_not_cache_blocked_results():
    engine = RuntimeEngine(runner=_StubRunner(), policy=_DenyPolicy())
    call = ToolCall(
        tool_call_id="tc-idem-block",
        name="exec",
        kind="exec",
        spec=ExecSpec(cmd=["echo"]),
        idempotency_key="replay-blocked-001",
    )
    r1 = engine.execute_tool_call(call, _ctx())
    r2 = engine.execute_tool_call(call, _ctx())
    assert r1.outcome == "blocked"
    assert r2.outcome == "blocked"
    assert not r2.from_cache
