from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.adapters.a2a import A2actlAdapter
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_FAILED,
    BRAIN_JOB_STATUS_RUNNING,
)
from openminion.modules.brain.adapters.a2a import LocalA2AAdapter


# (a) LocalA2AAdapter is echo-only


def test_local_a2a_adapter_sync_succeeds_for_any_target() -> None:
    adapter = LocalA2AAdapter()

    for target in ("planner-safe", "ops-safe", "nonexistent-agent-xyz"):
        result = adapter.call(
            command={
                "target_agent_id": target,
                "method": "run",
                "params": {"goal": "characterization-probe"},
            },
            session_id="s-char-01",
            trace_id="t-char-01",
        )
        assert result["status"] == BRAIN_ACTION_STATUS_SUCCESS, (
            f"LocalA2AAdapter returned non-success for {target!r}: {result}"
        )
        # Output echoes the input — no handler transformed it
        assert result["outputs"]["target_agent_id"] == target
        assert result["outputs"]["params"] == {"goal": "characterization-probe"}


def test_local_a2a_adapter_output_is_input_echo_not_handler_output() -> None:
    adapter = LocalA2AAdapter()

    sentinel_params = {"goal": "sentinel-value-123", "input_marker": True}
    result = adapter.call(
        command={
            "target_agent_id": "planner-safe",
            "method": "plan",
            "params": sentinel_params,
        },
        session_id="s-char-02",
        trace_id="t-char-02",
    )

    assert result["status"] == BRAIN_ACTION_STATUS_SUCCESS
    # Output is a direct echo of input — a real planner would produce plan steps
    assert result["outputs"]["params"] == sentinel_params
    assert result["outputs"]["target_agent_id"] == "planner-safe"
    assert "plan_steps" not in result["outputs"]
    assert "reasoning" not in result["outputs"]


def test_local_a2a_adapter_async_job_stays_running_forever() -> None:
    adapter = LocalA2AAdapter()

    start_result = adapter.call(
        command={
            "target_agent_id": "ops-safe",
            "method": "delegate",
            "params": {"goal": "probe"},
            "expect_async": True,
        },
        session_id="s-char-03",
        trace_id="t-char-03",
    )
    assert start_result["status"] == BRAIN_JOB_STATUS_RUNNING
    task_id = start_result["task_id"]
    assert task_id

    # Poll three times — status never transitions because no handler runs
    for _ in range(3):
        poll = adapter.poll_task(
            task_id=task_id,
            session_id="s-char-03",
            trace_id="t-char-03",
        )
        assert poll["status"] == BRAIN_JOB_STATUS_RUNNING, (
            "LocalA2AAdapter async job should stay RUNNING (no handler executes to complete it)"
        )


# (b) Builtin-only A2actlAdapter registration is not enough for
#     profile-backed delegation


class _BuiltinOnlyRuntime:
    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}
        self.call_attempts: list[str] = []

    def register_agent(
        self, agent_id: str, capabilities: Any, handler: Any, *, tags: Any = None
    ) -> None:
        self._agents[agent_id] = handler

    def call(self, envelope: Any) -> Any:
        target = str(getattr(envelope, "to_agent", "") or "").strip()
        self.call_attempts.append(target)
        if target not in self._agents:
            raise RuntimeError(f"Agent not registered: {target!r}")
        result = self._agents[target](envelope)
        return SimpleNamespace(
            from_agent=target,
            method=getattr(envelope, "method", ""),
            params={"ok": True, "data": result},
        )

    def job_start(self, envelope: Any) -> str:
        target = str(getattr(envelope, "to_agent", "") or "").strip()
        self.call_attempts.append(target)
        if target not in self._agents:
            raise RuntimeError(f"Agent not registered: {target!r}")
        return "job-builtin-1"


def _adapter_with_builtins_only() -> tuple[A2actlAdapter, _BuiltinOnlyRuntime]:
    runtime = _BuiltinOnlyRuntime()

    # Replicate exactly what _register_builtin_agents does
    def echo_handler(envelope: Any) -> dict[str, Any]:
        return {
            "agent": "agent.echo",
            "method": getattr(envelope, "method", ""),
            "params": getattr(envelope, "params", {}),
        }

    def worker_handler(envelope: Any) -> dict[str, Any]:
        return {
            "agent": "agent.worker",
            "method": getattr(envelope, "method", ""),
        }

    runtime.register_agent(
        "agent.echo", ["echo.", "debug."], echo_handler, tags=["builtin"]
    )
    runtime.register_agent(
        "agent.worker", ["job.", "sleep."], worker_handler, tags=["builtin"]
    )

    adapter = A2actlAdapter(agent_id="hello-agent")
    adapter._ensure_runtime = lambda: runtime  # type: ignore[method-assign]
    return adapter, runtime


def test_builtin_only_runtime_fails_for_planner_safe() -> None:
    adapter, runtime = _adapter_with_builtins_only()

    result = adapter.call(
        command={
            "target_agent_id": "planner-safe",
            "method": "plan",
            "params": {"goal": "test"},
            "expect_async": False,
        },
        session_id="s-char-04",
        trace_id="t-char-04",
    )

    assert result["status"] != BRAIN_ACTION_STATUS_SUCCESS, (
        "Expected failure for unregistered profile-backed target planner-safe, "
        f"but got: {result}"
    )
    assert "planner-safe" in runtime.call_attempts


def test_builtin_only_runtime_fails_for_ops_safe() -> None:
    adapter, runtime = _adapter_with_builtins_only()

    result = adapter.call(
        command={
            "target_agent_id": "ops-safe",
            "method": "run",
            "params": {"goal": "test"},
            "expect_async": False,
        },
        session_id="s-char-05",
        trace_id="t-char-05",
    )

    assert result["status"] != BRAIN_ACTION_STATUS_SUCCESS, (
        "Expected failure for unregistered profile-backed target ops-safe, "
        f"but got: {result}"
    )
    assert "ops-safe" in runtime.call_attempts


# (c) Missing-target path fails deterministically


def test_missing_target_fails_deterministically() -> None:
    adapter, _ = _adapter_with_builtins_only()

    result = adapter.call(
        command={
            "target_agent_id": "nonexistent-agent-xyz",
            "method": "run",
            "params": {},
        },
        session_id="s-char-06",
        trace_id="t-char-06",
    )

    assert result["status"] in (
        BRAIN_ACTION_STATUS_FAILED,
        BRAIN_JOB_STATUS_FAILED,
        "failed",
    ), f"Expected deterministic failure for missing target, got: {result}"
    assert result["status"] != BRAIN_ACTION_STATUS_SUCCESS
    assert "error" in result or "summary" in result


def test_local_vs_real_adapter_diverge_on_missing_target() -> None:
    missing_target = "ghost-agent-never-registered"
    command = {
        "target_agent_id": missing_target,
        "method": "run",
        "params": {},
    }

    # LocalA2AAdapter: silently succeeds — the proof gap
    local_adapter = LocalA2AAdapter()
    local_result = local_adapter.call(
        command=command,
        session_id="s-char-07",
        trace_id="t-char-07",
    )
    assert local_result["status"] == BRAIN_ACTION_STATUS_SUCCESS, (
        "LocalA2AAdapter must succeed (echo) for any target — this is the proof gap"
    )

    # A2actlAdapter: fails deterministically — the real contract
    real_adapter, _ = _adapter_with_builtins_only()
    real_result = real_adapter.call(
        command=command,
        session_id="s-char-07",
        trace_id="t-char-07",
    )
    assert real_result["status"] != BRAIN_ACTION_STATUS_SUCCESS, (
        "A2actlAdapter must fail for unregistered target — real runtime rejects unknown agents"
    )


# (d) Config-backed runtime registration enables real profile-backed delegation


class _ConfiguredRuntimeHandle:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_registered_agents(self) -> list[str]:
        return ["planner-safe", "ops-safe"]

    def run_turn(
        self, *, payload: dict[str, Any], request_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append({"payload": dict(payload), "request_id": request_id})
        return {
            "id": "turn-delegate-1",
            "channel": "console",
            "target": "a2a",
            "body": "delegate ok",
            "session_id": str(payload.get("session_id", "") or ""),
            "run_id": "run-delegate-1",
            "metadata": {"session_id": str(payload.get("session_id", "") or "")},
            "agent_id": str(payload.get("agent_id", "") or ""),
        }


def test_runtime_backed_registration_serves_configured_delegate_target(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    runtime_handle = _ConfiguredRuntimeHandle()
    adapter = A2actlAdapter(
        home_root=tmp_path,
        agent_id="router-agent",
        runtime_resolver=lambda: runtime_handle,
    )
    try:
        result = adapter.call(
            command={
                "target_agent_id": "planner-safe",
                "method": "delegate",
                "params": {
                    "goal": "summarize the repo status",
                    "summary": "focus on active branch changes",
                    "constraints": ["keep it brief", "mention tests"],
                    "target_capability": "research",
                },
            },
            session_id="s-configured-01",
            trace_id="t-configured-01",
        )
    finally:
        adapter.close()

    assert result["status"] == BRAIN_ACTION_STATUS_SUCCESS
    assert result["summary"] == "delegate ok"
    assert result["outputs"]["summary"] == "delegate ok"
    assert runtime_handle.calls
    payload = runtime_handle.calls[0]["payload"]
    assert payload["agent_id"] == "planner-safe"
    assert payload["deliver"] is False
    assert payload["capability_category"] == "research"
    assert "summarize the repo status" in payload["message"]
    assert "focus on active branch changes" in payload["message"]
    assert "keep it brief" in payload["message"]
    assert "mention tests" in payload["message"]
    assert payload["session_id"] == "s-configured-01::delegate::planner-safe"
