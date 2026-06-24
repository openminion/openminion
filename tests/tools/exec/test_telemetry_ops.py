from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterable

import pytest

from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.diagnostics.events import (
    emit_tool_exec_operation,
)
from openminion.tools.exec.plugin import _h_exec_run, _h_process_kill, _h_process_poll
from openminion.tools.exec.process import PROCESS_MANAGER


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    return str(tmp_path / ".openminion" / "telemetry.db")


@pytest.fixture(autouse=True)
def _cleanup_sessions() -> Iterable[None]:
    PROCESS_MANAGER._reset_for_tests()
    try:
        yield
    finally:
        PROCESS_MANAGER._reset_for_tests()


def _ctx(
    tmp_path,
    *,
    telemetryctl: TelemetryCtl | None,
    telemetry_session_id: str,
    telemetry_turn_id: str = "turn-1",
) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "runs"),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {
                "mode": "allowlist",
                "allow": [
                    "bash",
                    "zsh",
                    "sh",
                    "pwsh",
                    "powershell",
                    "cmd.exe",
                    "printf",
                    "sleep",
                    "echo",
                    "cat",
                    "pwd",
                ],
                "deny_exact": [],
                "deny_regex": [],
            },
            "env": {"allow_keys": ["PATH", "HOME"], "deny_keys_regex": []},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
        telemetryctl=telemetryctl,
        telemetry_session_id=telemetry_session_id,
        telemetry_turn_id=telemetry_turn_id,
    )


def _module_events(summary: Any, *, module_id: str) -> list[dict[str, Any]]:
    return [
        event.data
        for event in summary.events
        if isinstance(event.data, dict) and event.data.get("module_id") == module_id
    ]


def _status_counts(events: list[dict[str, Any]], *, operation: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payload in events:
        if payload.get("operation") != operation:
            continue
        status = str(payload.get("status", ""))
        counts[status] = counts.get(status, 0) + 1
    return counts


async def _await_module_summary(
    service: TelemetryService,
    *,
    session_id: str,
    module_id: str,
    expected_counts: dict[str, int],
) -> tuple[Any, dict[str, dict[str, Any]]]:
    last_summary = await service.get_session_summary(session_id)
    last_module_summary = await service.get_module_summary(session_id)
    for _ in range(20):
        stats = last_module_summary.get(module_id, {})
        op_counts = stats.get("operation_counts", {})
        if all(
            op_counts.get(name, 0) >= count for name, count in expected_counts.items()
        ):
            return await service.get_session_summary(session_id), last_module_summary
        await asyncio.sleep(0.01)
        last_summary = await service.get_session_summary(session_id)
        last_module_summary = await service.get_module_summary(session_id)
    return last_summary, last_module_summary


def test_tool_exec_module_emits_run_poll_stop_and_kill_operations(
    temp_db: str, tmp_path
) -> None:
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)
        ctx = _ctx(
            tmp_path,
            telemetryctl=ctl,
            telemetry_session_id="sess-tool-ops",
        )

        started = _h_exec_run({"command": "sleep 5", "background": True}, ctx)
        assert started["status"] == "running"
        session_stop = str(started["session_id"])

        polled = _h_process_poll({"session_id": session_stop, "tail_lines": 20}, ctx)
        assert polled["status"] == "running"

        stopped = _h_process_kill({"session_id": session_stop}, ctx)
        assert stopped["status"] == "ok"

        started_kill = _h_exec_run({"command": "sleep 5", "background": True}, ctx)
        assert started_kill["status"] == "running"
        session_kill = str(started_kill["session_id"])

        killed = _h_process_kill(
            {"session_id": session_kill, "signal": "KILL"},
            ctx,
        )
        assert killed["status"] == "ok"

        missing_poll = _h_process_poll(
            {"session_id": "missing-poll", "tail_lines": 20}, ctx
        )
        assert missing_poll["status"] == "killed"

        missing_stop = _h_process_kill({"session_id": "missing-stop"}, ctx)
        assert missing_stop["status"] == "error"

        missing_kill = _h_process_kill(
            {"session_id": "missing-kill", "signal": "KILL"},
            ctx,
        )
        assert missing_kill["status"] == "error"

        summary, module_summary = await _await_module_summary(
            service,
            session_id="sess-tool-ops",
            module_id="openminion-tool",
            expected_counts={"run": 2, "poll": 2, "stop": 2, "kill": 2},
        )
        stats = module_summary["openminion-tool"]
        assert stats["operation_counts"]["run"] == 2
        assert stats["operation_counts"]["poll"] == 2
        assert stats["operation_counts"]["stop"] == 2
        assert stats["operation_counts"]["kill"] == 2

        events = _module_events(summary, module_id="openminion-tool")
        assert _status_counts(events, operation="run") == {"running": 2}
        assert _status_counts(events, operation="poll") == {"running": 1, "error": 1}
        assert _status_counts(events, operation="stop") == {"killed": 1, "error": 1}
        assert _status_counts(events, operation="kill") == {"killed": 1, "error": 1}

        await service.close()

    _run(_case())


def test_tool_exec_module_emits_run_errors_and_timeout(temp_db: str, tmp_path) -> None:
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)
        ctx = _ctx(
            tmp_path,
            telemetryctl=ctl,
            telemetry_session_id="sess-tool-timeout",
        )

        bad_workdir = _h_exec_run({"command": "pwd", "workdir": "missing"}, ctx)
        assert bad_workdir["status"] == "error"
        assert bad_workdir["error"]["code"] == "INVALID_ARGUMENT"

        timed_out = _h_exec_run(
            {"command": "sleep 1.2", "timeout_s": 1, "yield_ms": 2000},
            ctx,
        )
        assert timed_out["status"] == "timeout"
        assert timed_out["error"]["code"] == "TIMEOUT"

        summary, module_summary = await _await_module_summary(
            service,
            session_id="sess-tool-timeout",
            module_id="openminion-tool",
            expected_counts={"run": 2, "timeout": 1},
        )
        stats = module_summary["openminion-tool"]
        assert stats["operation_counts"]["run"] == 2
        assert stats["operation_counts"]["timeout"] == 1

        events = _module_events(summary, module_id="openminion-tool")
        assert _status_counts(events, operation="run") == {"error": 2}
        assert _status_counts(events, operation="timeout") == {"error": 1}

        await service.close()

    _run(_case())


def test_tool_exec_telemetry_helper_rejects_invalid_name_and_absent_adapter(
    temp_db: str, tmp_path
) -> None:
    async def _case() -> None:
        service = TelemetryService(temp_db)
        ctl = TelemetryCtl(service)

        assert (
            emit_tool_exec_operation(
                telemetryctl=ctl,
                session_id="sess-tool-invalid",
                turn_id="turn-1",
                operation="not-real",
                tool_name="exec.run",
            )
            is False
        )
        assert (
            emit_tool_exec_operation(
                telemetryctl=None,
                session_id="sess-tool-invalid",
                turn_id="turn-1",
                operation="run",
                tool_name="exec.run",
            )
            is False
        )

        ctx = _ctx(
            tmp_path,
            telemetryctl=None,
            telemetry_session_id="sess-tool-invalid",
        )
        missing_poll = _h_process_poll({"session_id": "missing", "tail_lines": 10}, ctx)
        assert missing_poll["status"] == "killed"
        assert missing_poll["error"]["code"] == "NOT_FOUND"

        with pytest.raises(ValueError, match="operation must be non-empty"):
            await ctl.emit_module_operation(
                session_id="sess-tool-invalid",
                turn_id="turn-1",
                module_id="openminion-tool",
                operation="",
            )

        module_summary = await service.get_module_summary("sess-tool-invalid")
        assert module_summary == {}
        await service.close()

    _run(_case())
