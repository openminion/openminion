from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.api.server import dispatch_request
from openminion.base.config import OpenMinionConfig
from openminion.cli.commands import config as config_command
from openminion.cli.commands import debug as debug_package
from openminion.cli.commands import status as status_command
from openminion.cli.commands import storage as storage_command
from openminion.cli.commands import service as service_command
from openminion.cli.commands import sidecar as sidecar_command
from openminion.cli.commands import tools as tools_command
from openminion.cli.commands.debug import cli as debug_command
from openminion.cli.commands.run import run_openminion
from openminion.cli.parser.base import build_parser
from openminion.cli.transport.runtime_source import call_daemon_or_inproc


def test_retired_interactive_commands_remain_rejected() -> None:
    parser = build_parser()
    for command in ("chat", "dashboard", "focus", "tui"):
        with pytest.raises(SystemExit):
            parser.parse_args([command])


def test_run_help_uses_profile_as_canonical_selector(capsys) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--help"])
    help_text = capsys.readouterr().out
    assert "--profile" in help_text
    assert "--agent" in help_text
    assert "Configured profile id" in help_text


def test_runtime_source_inproc_bypasses_daemon() -> None:
    args = Namespace(runtime_source="inproc")
    result = call_daemon_or_inproc(
        args=args,
        auto_start=True,
        daemon_call=lambda _endpoint: (_ for _ in ()).throw(
            AssertionError("daemon touched")
        ),
        inproc_call=lambda: {"ok": True, "value": "local"},
    )

    assert result.source == "inproc"
    assert result.payload == {"ok": True, "value": "local"}


def test_runtime_source_daemon_fails_closed_when_unavailable(monkeypatch) -> None:
    import openminion.cli.transport.runtime_source as runtime_source

    monkeypatch.setattr(
        runtime_source,
        "ensure_daemon_running",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("down")),
    )

    with pytest.raises(RuntimeError, match="daemon runtime source unavailable: down"):
        call_daemon_or_inproc(
            args=Namespace(runtime_source="daemon", config=None),
            auto_start=False,
            daemon_call=lambda _endpoint: (200, {"ok": True}),
            inproc_call=lambda: {"ok": True},
        )


def test_tools_auto_reports_inproc_fallback(monkeypatch) -> None:
    config = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=False))
    monkeypatch.setattr(
        tools_command, "load_cli_config_from_args", lambda _args: config
    )
    monkeypatch.setattr(
        tools_command,
        "call_daemon_or_inproc",
        lambda **_kwargs: SimpleNamespace(
            source="inproc",
            payload={"ok": True, "tools": []},
            fallback_reason="daemon down",
        ),
    )

    payload = tools_command._from_daemon_or_inproc(
        Namespace(runtime_source="auto", config=None),
        daemon_call=lambda _endpoint: (200, {"ok": True}),
        inproc_call=lambda: {"ok": True, "tools": []},
    )

    assert payload["runtime_source"] == "inproc"
    assert payload["runtime_fallback_reason"] == "daemon down"


def test_service_list_json_surfaces_major_runtime_services(capsys) -> None:
    code = service_command.run_service(Namespace(service_command="list", json=True))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload["services"]] == [
        "daemon",
        "api",
        "gateway",
        "cron",
        "sidecar",
    ]


def test_sidecar_human_status_is_operator_readable(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sidecar_command,
        "load_cli_config_from_args",
        lambda _args: SimpleNamespace(
            runtime=SimpleNamespace(env={}),
            security=SimpleNamespace(
                tool_policy=SimpleNamespace(
                    max_calls_per_run=1,
                    max_calls_per_tool=1,
                    max_budget_cost_per_run=1,
                    default_required_scopes=[],
                )
            ),
        ),
    )
    monkeypatch.setattr(
        sidecar_command,
        "_build_manager",
        lambda *_args, **_kwargs: _FakeSidecarManager(),
    )

    code = sidecar_command.run_sidecar(
        Namespace(sidecar_command="status", name="", json=False, config=None)
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "sidecar status: count=1" in output
    assert "pinchtab" in output
    assert "consent=approved" in output


def test_sidecar_restart_uses_stop_then_start(monkeypatch, capsys) -> None:
    manager = _FakeSidecarManager()
    monkeypatch.setattr(
        sidecar_command,
        "load_cli_config_from_args",
        lambda _args: SimpleNamespace(
            runtime=SimpleNamespace(env={}),
            security=SimpleNamespace(
                tool_policy=SimpleNamespace(
                    max_calls_per_run=1,
                    max_calls_per_tool=1,
                    max_budget_cost_per_run=1,
                    default_required_scopes=[],
                )
            ),
        ),
    )
    monkeypatch.setattr(
        sidecar_command, "_build_manager", lambda *_args, **_kwargs: manager
    )

    code = sidecar_command.run_sidecar(
        Namespace(
            sidecar_command="restart",
            name="pinchtab",
            json=True,
            config=None,
            kill=False,
            no_prompt=True,
        )
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "restart"
    assert manager.actions == ["stop:pinchtab", "start:pinchtab"]


def test_health_liveness_and_readiness_paths_are_distinct() -> None:
    live_status, live_payload = dispatch_request("GET", "/v1/live", None)
    ready_status, ready_payload = dispatch_request(
        "GET",
        "/v1/ready",
        None,
        runtime_bootstrap_error="bootstrap failed",
    )

    assert int(live_status) == 200
    assert live_payload["kind"] == "liveness"
    assert int(ready_status) == 503
    assert ready_payload["kind"] == "readiness"
    assert ready_payload["degraded"] is True


def test_run_daemon_source_error_is_operator_visible(monkeypatch) -> None:
    config = OpenMinionConfig()
    config.runtime.process_mode = "daemon"
    config.runtime.daemon_auto_start = False
    monkeypatch.setattr(
        "openminion.cli.commands.run._load_run_config",
        lambda _args: config,
    )
    monkeypatch.setattr(
        "openminion.cli.commands.run.call_daemon_or_inproc",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("daemon runtime source unavailable: down")
        ),
    )

    with pytest.raises(RuntimeError, match="daemon runtime source unavailable: down"):
        run_openminion(
            Namespace(
                prompt="hello",
                json=True,
                runtime_source="daemon",
                config=None,
                home_root=None,
                data_root=None,
                agent="agent.test",
                session=None,
                purpose="",
                resume=False,
                reset_session=False,
                stream=False,
            )
        )


def test_config_show_uses_root_aware_loader(monkeypatch, capsys) -> None:
    calls: list[tuple[object, Path, Path]] = []
    home_root = Path("/tmp/openminion-home")
    data_root = home_root / ".openminion"

    monkeypatch.setattr(
        config_command,
        "resolve_cli_roots",
        lambda **_kwargs: SimpleNamespace(home_root=home_root, data_root=data_root),
    )
    monkeypatch.setattr(
        config_command,
        "load_cli_config",
        lambda config_path, *, home_root, data_root: calls.append(
            (config_path, home_root, data_root)
        )
        or SimpleNamespace(to_dict=lambda: {"ok": True}),
    )

    code = config_command.config_show(
        Namespace(config="agents.json", home_root=str(home_root), data_root=str(data_root))
    )

    assert code == 0
    assert calls == [("agents.json", home_root, data_root)]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_storage_status_uses_root_aware_config_loader(monkeypatch, capsys) -> None:
    calls: list[object] = []
    fake_engine = _FakeStorageEngine()
    monkeypatch.setattr(
        storage_command,
        "load_cli_config_from_args",
        lambda args: calls.append(args)
        or SimpleNamespace(storage=SimpleNamespace(path="/tmp/openminion.db")),
    )

    import openminion.modules.storage.engine as storage_engine

    monkeypatch.setattr(
        storage_engine.StorageEngine,
        "from_paths",
        lambda **_kwargs: fake_engine,
    )
    args = Namespace(
        config="agents.json",
        home_root="/tmp/home",
        data_root="/tmp/home/.openminion",
        sqlite=None,
        root=None,
        fallback=None,
        namespace="session",
        storage_command="status",
        json=True,
    )

    code = storage_command.run_storage(args)

    assert code == 0
    assert calls == [args]
    assert fake_engine.closed is True
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_debug_modules_passes_roots_to_daemon_probe(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []
    config = SimpleNamespace(runtime=SimpleNamespace(daemon_auto_start=True))
    monkeypatch.setattr(debug_command, "_load_debug_config", lambda _args: config)
    monkeypatch.setattr(
        debug_command,
        "is_debug_surface_enabled",
        lambda _config, *, surface: surface == "cli",
    )
    monkeypatch.setattr(
        debug_command,
        "register_core_providers",
        lambda _registry: None,
    )
    monkeypatch.setattr(
        debug_command,
        "get_debug_registry",
        lambda: SimpleNamespace(get_all_debug=lambda: []),
    )

    def fake_ensure_daemon_running(config_path: object, **kwargs: object) -> object:
        calls.append({"config_path": config_path, **kwargs})
        return object()

    monkeypatch.setattr(
        "openminion.cli.commands.daemon.ensure_daemon_running",
        fake_ensure_daemon_running,
    )
    monkeypatch.setattr(
        debug_command,
        "daemon_request",
        lambda **_kwargs: (200, {"ok": True, "modules": []}),
    )

    code = debug_command.run_debug(
        Namespace(
            debug_command="modules",
            config="agents.json",
            home_root="/tmp/home",
            data_root="/tmp/home/.openminion",
            json=True,
        )
    )

    assert code == 0
    assert calls == [
        {
            "config_path": "agents.json",
            "auto_start": True,
            "home_root": "/tmp/home",
            "data_root": "/tmp/home/.openminion",
        }
    ]
    assert json.loads(capsys.readouterr().out) == {"ok": True, "modules": []}


def test_debug_package_wrapper_uses_root_aware_default_loader(monkeypatch) -> None:
    calls: list[object] = []
    disabled_config = SimpleNamespace(
        runtime=SimpleNamespace(debug_enabled=False, debug_cli_enabled=True)
    )

    def fake_load_cli_config_from_args(args: object) -> object:
        calls.append(args)
        return disabled_config

    monkeypatch.setattr(
        debug_package,
        "load_cli_config_from_args",
        fake_load_cli_config_from_args,
    )
    monkeypatch.setattr(
        debug_package,
        "load_config",
        fake_load_cli_config_from_args,
    )
    args = Namespace(
        debug_command="modules",
        config="agents.json",
        home_root="/tmp/home",
        data_root="/tmp/home/.openminion",
        json=True,
    )

    assert debug_package.run_debug(args) == 1
    assert calls == [args]


def test_local_debug_provider_uses_configured_roots(monkeypatch) -> None:
    from openminion.cli.commands.debug.providers import core as core_provider

    calls: list[dict[str, object]] = []

    class FakeRuntime:
        tools = SimpleNamespace(provider_specs=lambda: [])

        def close(self) -> None:
            calls.append({"closed": True})

    def fake_from_config_path(
        config_path: object,
        *,
        home_root: object = None,
        data_root: object = None,
    ) -> FakeRuntime:
        calls.append(
            {
                "config_path": config_path,
                "home_root": home_root,
                "data_root": data_root,
            }
        )
        return FakeRuntime()

    monkeypatch.setattr(
        core_provider.APIRuntime,
        "from_config_path",
        fake_from_config_path,
    )
    try:
        core_provider.configure_debug_runtime_context(
            config_path="agents.json",
            home_root="/tmp/home",
            data_root="/tmp/home/.openminion",
        )

        payload = core_provider.OpenMinionToolsDebugProvider()._probe().to_dict()

        assert payload["status"] == "ok"
        assert calls == [
            {
                "config_path": "agents.json",
                "home_root": "/tmp/home",
                "data_root": "/tmp/home/.openminion",
            },
            {"closed": True},
        ]
    finally:
        core_provider.configure_debug_runtime_context(
            config_path=None,
            home_root=None,
            data_root=None,
        )


def test_debug_module_errors_write_to_stderr(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        debug_command,
        "_load_debug_config",
        lambda _args: SimpleNamespace(
            runtime=SimpleNamespace(debug_enabled=True, debug_cli_enabled=True)
        ),
    )
    monkeypatch.setattr(
        debug_command,
        "is_debug_surface_enabled",
        lambda _config, *, surface: surface == "cli",
    )

    code = debug_command.run_debug(
        Namespace(
            debug_command="module",
            module_name="",
            config=None,
            home_root=None,
            data_root=None,
            json=False,
        )
    )

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "Error: --name is required" in captured.err


def test_status_owner_passes_roots_to_query(monkeypatch, capsys) -> None:
    import openminion.api.queries.owner as owner_query

    calls: list[dict[str, object]] = []

    def fake_get_owner_status(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "heartbeat": {"status": "ok"},
            "summary": {"runs_total": 0, "failed_runs": 0, "active_runs": 0},
            "sessions_total": 0,
            "alerts": [],
            "recent_failures": [],
        }

    monkeypatch.setattr(status_command, "_load_status_config", lambda _args: object())
    monkeypatch.setattr(owner_query, "get_owner_status", fake_get_owner_status)

    code = status_command.run_status(
        Namespace(
            status_command="owner",
            config="agents.json",
            home_root="/tmp/home",
            data_root="/tmp/home/.openminion",
            session_limit=2,
            run_limit=3,
            hours=4,
            json=True,
        )
    )

    assert code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert calls == [
        {
            "config_path": "agents.json",
            "session_limit": 2,
            "run_limit_per_session": 3,
            "window_hours": 4,
            "home_root": "/tmp/home",
            "data_root": "/tmp/home/.openminion",
        }
    ]


def test_status_self_inproc_payload_uses_configured_roots(monkeypatch) -> None:
    from openminion.cli.commands.status import self as self_status

    calls: list[dict[str, object]] = []

    class FakeRuntime:
        def runtime_self_model(self) -> dict[str, object]:
            return {"health": "ok"}

        def close(self) -> None:
            calls.append({"closed": True})

    def fake_from_config_path(
        config_path: object,
        *,
        home_root: object = None,
        data_root: object = None,
    ) -> FakeRuntime:
        calls.append(
            {
                "config_path": config_path,
                "home_root": home_root,
                "data_root": data_root,
            }
        )
        return FakeRuntime()

    monkeypatch.setattr(
        self_status.APIRuntime,
        "from_config_path",
        fake_from_config_path,
    )

    payload = self_status._build_inproc_self_model_payload(
        Namespace(
            config="agents.json",
            home_root="/tmp/home",
            data_root="/tmp/home/.openminion",
        )
    )

    assert payload == {"ok": True, "self_model": {"health": "ok"}, "health": "ok"}
    assert calls == [
        {
            "config_path": "agents.json",
            "home_root": "/tmp/home",
            "data_root": "/tmp/home/.openminion",
        },
        {"closed": True},
    ]


class _FakeSidecarManager:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def list(self) -> list[str]:
        return ["pinchtab"]

    def specs(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                name="pinchtab",
                autostart_env_key="PINCHTAB_AUTOSTART",
            )
        ]

    def status(self, name: str) -> dict[str, object]:
        return {"ok": True, "pid_alive": True, "sidecar": name}

    def consent(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            name=name,
            approved=True,
            approved_at="2026-07-24T00:00:00Z",
            scope="persistent",
        )

    def ensure_started(self, *, name: str, interactive: bool) -> dict[str, object]:
        self.actions.append(f"start:{name}")
        return {"started": True, "interactive": interactive}

    def stop(self, *, name: str, kill: bool = False) -> dict[str, object]:
        self.actions.append(f"stop:{name}")
        return {"stopped": True, "kill": kill}


class _FakeStorageEngine:
    def __init__(self) -> None:
        self.closed = False

    def module(self, namespace: str) -> SimpleNamespace:
        return SimpleNamespace(
            status=lambda: {
                "namespace": namespace,
                "sqlite_ok": True,
                "fallback_mode": False,
            }
        )

    def close(self) -> None:
        self.closed = True
