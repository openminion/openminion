from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from openminion.cli.commands.agent.delegation import (
    AgentDelegateRequest,
    request_from_slash_args,
    run_agent_delegate_request,
)
from openminion.cli.commands.agent.control import (
    agent_delegate,
    agent_ls,
    agent_status,
    run_agent_operator,
)
from openminion.cli.parser.base import build_parser
from openminion.modules.tool.runtime.delegation import A2ADelegateResult


class _DelegateSeam:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def delegate(
        self,
        *,
        agent_id,
        instruction,
        timeout_seconds,
        mode="sync",
        permission_mode="ask",
        workspace_root="",
        cwd="",
    ) -> A2ADelegateResult:
        self.calls.append(
            (
                "delegate",
                {
                    "agent_id": agent_id,
                    "instruction": instruction,
                    "timeout_seconds": timeout_seconds,
                    "mode": mode,
                    "permission_mode": permission_mode,
                    "workspace_root": workspace_root,
                    "cwd": cwd,
                },
            )
        )
        return A2ADelegateResult(
            ok=True,
            status="running" if mode == "async" else "success",
            content="delegated ok",
            target_agent_id=agent_id,
            trace_id="trace-1",
            task_id="task-1" if mode == "async" else "",
        )

    def status(self, *, task_id) -> A2ADelegateResult:
        self.calls.append(("status", {"task_id": task_id}))
        return A2ADelegateResult(
            ok=True,
            status="running",
            content="still running",
            task_id=task_id,
        )

    def resume(self, *, task_id) -> A2ADelegateResult:
        self.calls.append(("resume", {"task_id": task_id}))
        return A2ADelegateResult(
            ok=True,
            status="success",
            content="final result",
            task_id=task_id,
        )

    def cancel(self, *, task_id) -> A2ADelegateResult:
        self.calls.append(("cancel", {"task_id": task_id}))
        return A2ADelegateResult(
            ok=True,
            status="canceled",
            content="cancelled",
            task_id=task_id,
        )


def test_agent_ls_json_output(capsys) -> None:
    registry = SimpleNamespace(
        list_agents=lambda: [
            SimpleNamespace(agent_id="agent-1", display_name="Agent One"),
            SimpleNamespace(agent_id="agent-2", display_name="Agent Two"),
        ],
        list_heartbeats=lambda: [
            SimpleNamespace(
                agent_id="agent-1",
                status="running",
                pid=111,
                host="127.0.0.1",
                port=8001,
            )
        ],
        is_agent_stale=lambda agent_id: agent_id == "agent-2",
    )

    code = agent_ls(registry, as_json=True)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "agent_id": "agent-1",
            "display_name": "Agent One",
            "configured": False,
            "registry_present": True,
            "hot": True,
            "heartbeat_active": True,
            "available": True,
            "running": True,
            "stopped": False,
            "unknown": False,
            "state": "running",
            "host": "127.0.0.1",
            "pid": 111,
            "port": 8001,
            "status": "running",
        },
        {
            "agent_id": "agent-2",
            "display_name": "Agent Two",
            "configured": False,
            "registry_present": True,
            "hot": False,
            "heartbeat_active": False,
            "available": True,
            "running": False,
            "stopped": True,
            "unknown": False,
            "state": "stopped",
            "host": "",
            "pid": 0,
            "port": 0,
            "status": "stopped",
        },
    ]


def test_agent_status_json_output(capsys) -> None:
    heartbeat = SimpleNamespace(
        status="running",
        pid=222,
        host="127.0.0.1",
        port=9001,
        active_run_id="run-1",
        started_at="2026-06-03T00:00:00",
        last_heartbeat_at="2026-06-03T00:01:00",
    )
    registry = SimpleNamespace(
        get_agent=lambda _agent_id: SimpleNamespace(display_name="Agent One"),
        get_heartbeat=lambda _agent_id: heartbeat,
        is_agent_stale=lambda _agent_id: False,
    )

    code = agent_status(registry, "agent-1", as_json=True)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "agent_id": "agent-1",
        "display_name": "Agent One",
        "heartbeat": {
            "active_run_id": "run-1",
            "host": "127.0.0.1",
            "last_heartbeat_at": "2026-06-03T00:01:00",
            "pid": 222,
            "port": 9001,
            "started_at": "2026-06-03T00:00:00",
        },
        "registered": True,
        "status": "running",
    }


def test_agent_delegate_sync_json_uses_delegate_seam(capsys) -> None:
    seam = _DelegateSeam()
    expected_workspace = str(Path(".").resolve())

    code = agent_delegate(
        config=SimpleNamespace(),
        home_root="/tmp/home",
        parent_agent_id="parent",
        request=AgentDelegateRequest(
            mode="sync",
            target_agent_id="worker",
            instruction="summarize docs",
            timeout_seconds=42,
        ),
        as_json=True,
        delegate_api=seam,
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["agent_id"] == "worker"
    assert payload["mode"] == "sync"
    assert payload["status"] == "success"
    assert seam.calls == [
        (
            "delegate",
            {
                "agent_id": "worker",
                "instruction": "summarize docs",
                "timeout_seconds": 42,
                "mode": "sync",
                "permission_mode": "ask",
                "workspace_root": expected_workspace,
                "cwd": expected_workspace,
            },
        )
    ]


def test_agent_delegate_async_text_surfaces_task_handle(capsys) -> None:
    seam = _DelegateSeam()

    code = agent_delegate(
        config=SimpleNamespace(),
        home_root="/tmp/home",
        parent_agent_id="parent",
        request=AgentDelegateRequest(
            mode="async",
            target_agent_id="worker",
            instruction="run long research",
        ),
        as_json=False,
        delegate_api=seam,
    )

    body = capsys.readouterr().out
    assert code == 0
    assert "status    running" in body
    assert "task      task-1" in body


def test_agent_delegate_lifecycle_modes_use_task_id(capsys) -> None:
    seam = _DelegateSeam()
    modes = ("status", "resume", "result", "cancel")
    payloads: list[dict[str, object]] = []

    for mode in modes:
        code = agent_delegate(
            config=SimpleNamespace(),
            home_root="/tmp/home",
            parent_agent_id="parent",
            request=AgentDelegateRequest(mode=mode, task_id="task-1"),
            as_json=True,
            delegate_api=seam,
        )
        assert code == 0
        payloads.append(json.loads(capsys.readouterr().out))

    assert [payload["status"] for payload in payloads] == [
        "running",
        "success",
        "success",
        "canceled",
    ]
    assert [name for name, _payload in seam.calls] == [
        "status",
        "resume",
        "resume",
        "cancel",
    ]


def test_focus_delegate_accept_parses_artifact_json() -> None:
    request = request_from_slash_args(
        'accept \'{"subtask_id": "child-1", "artifact": {"status": "stored"}}\''
    )

    assert request.mode == "accept"
    assert request.child_artifact == {
        "subtask_id": "child-1",
        "artifact": {"status": "stored"},
    }


def test_visible_agent_delegate_command_uses_operator_seam(capsys, monkeypatch) -> None:
    import openminion.cli.commands.agent.control as agents_mod

    seen: dict[str, object] = {}
    config = SimpleNamespace(
        storage=SimpleNamespace(path="/tmp/openminion-test-storage"),
        runtime=SimpleNamespace(env={}),
    )

    def _fake_delegate_request(**kwargs):
        seen.update(kwargs)
        request = kwargs["request"]
        return {
            "ok": True,
            "mode": request.mode,
            "status": "success",
            "agent_id": request.target_agent_id,
            "content": "delegated from visible agent command",
            "trace_id": "trace-visible",
        }

    monkeypatch.setattr(agents_mod, "load_cli_config", lambda _path: config)
    monkeypatch.setattr(
        agents_mod,
        "resolve_cli_roots",
        lambda: SimpleNamespace(home_root="/tmp/openminion-home"),
    )
    monkeypatch.setattr(
        agents_mod,
        "AgentRegistryStore",
        lambda _path: SimpleNamespace(),
    )
    monkeypatch.setattr(agents_mod, "_default_agent_id", lambda _config: "parent")
    monkeypatch.setattr(
        agents_mod, "run_agent_delegate_request", _fake_delegate_request
    )

    args = build_parser().parse_args(
        [
            "agent",
            "delegate",
            "--target-agent-id",
            "worker",
            "--instruction",
            "do work",
            "--json",
        ]
    )
    code = args.handler(args)

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["agent_id"] == "worker"
    assert payload["content"] == "delegated from visible agent command"
    request = seen["request"]
    assert request.target_agent_id == "worker"
    assert request.instruction == "do work"
    assert seen["parent_agent_id"] == "parent"
    assert seen["home_root"] == "/tmp/openminion-home"


def test_agent_list_alias_dispatches_to_agent_ls(capsys, monkeypatch) -> None:
    import openminion.cli.commands.agent.control as agents_mod

    registry = SimpleNamespace(
        list_agents=lambda: [SimpleNamespace(agent_id="agent-1", display_name="Agent")],
        list_heartbeats=lambda: [],
        is_agent_stale=lambda _agent_id: False,
    )
    monkeypatch.setattr(
        agents_mod,
        "load_cli_config",
        lambda _path: SimpleNamespace(
            storage=SimpleNamespace(path="/tmp/openminion-test-storage"),
            runtime=SimpleNamespace(env={}),
        ),
    )
    monkeypatch.setattr(
        agents_mod,
        "resolve_cli_roots",
        lambda: SimpleNamespace(home_root="/tmp/openminion-home"),
    )
    monkeypatch.setattr(agents_mod, "AgentRegistryStore", lambda _path: registry)

    args = build_parser().parse_args(["agent", "list", "--json"])
    code = run_agent_operator(args)

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload[0]["agent_id"] == "agent-1"


def test_agent_delegate_unavailable_seam_returns_failure(capsys, monkeypatch) -> None:
    import openminion.cli.commands.agent.delegation as delegation_mod

    monkeypatch.setattr(delegation_mod, "build_a2a_delegate_api", lambda **_: None)

    code = agent_delegate(
        config=SimpleNamespace(runtime=SimpleNamespace(env={})),
        home_root="/tmp/home",
        parent_agent_id="parent",
        request=AgentDelegateRequest(
            mode="sync",
            target_agent_id="worker",
            instruction="do work",
        ),
        as_json=True,
        delegate_api=None,
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "DEPENDENCY_MISSING"


def test_delegate_request_threads_runtime_resolver_to_a2a_builder(monkeypatch) -> None:
    import openminion.cli.commands.agent.delegation as delegation_mod

    seam = _DelegateSeam()
    seen: dict[str, object] = {}
    runtime = object()
    approval_callback = object()

    def _fake_builder(**kwargs):
        seen.update(kwargs)
        return seam

    monkeypatch.setattr(delegation_mod, "build_a2a_delegate_api", _fake_builder)

    payload = run_agent_delegate_request(
        config=SimpleNamespace(runtime=SimpleNamespace(env={})),
        home_root="/tmp/home",
        parent_agent_id="parent",
        request=AgentDelegateRequest(
            mode="sync",
            target_agent_id="worker",
            instruction="do work",
        ),
        runtime_resolver=lambda: runtime,
        approval_callback=approval_callback,
    )

    assert payload["ok"] is True
    assert seen["runtime_resolver"]() is runtime
    assert seen["approval_callback"] is approval_callback
