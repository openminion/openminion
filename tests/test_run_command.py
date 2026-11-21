from __future__ import annotations

import json
import logging
import pytest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.cli.commands import run as run_command


def _run_args(*, as_json: bool = False) -> Namespace:
    return Namespace(
        prompt="hello",
        file="",
        config=None,
        agent="",
        session="",
        purpose="",
        resume=False,
        reset_session=False,
        stream=False,
        jsonl=False,
        json=as_json,
    )


def _run_config(*, process_mode: str = "single-process") -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            process_mode=process_mode,
            daemon_auto_start=True,
            chat_turn_timeout_seconds=300,
        ),
        gateway=SimpleNamespace(api_turn_timeout_seconds=45),
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["--session", "probe-x", "run", "hi"],
        ["run", "--session", "probe-x", "hi"],
    ],
)
def test_run_payload_carries_session_from_both_flag_positions(
    monkeypatch, argv: list[str]
) -> None:
    from openminion.cli.parser.base import build_parser

    args = build_parser().parse_args(argv)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        run_command, "load_cli_config_from_args", lambda _args: _run_config()
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs) or {"run_id": "trace-1", "final_text": "done"}
        ),
    )

    assert run_command.run_openminion(args) == 0
    assert captured["payload"]["session_id"] == "probe-x"


def test_run_payload_defaults_to_cli_run_without_session(monkeypatch, capsys) -> None:
    from openminion.cli.parser.base import build_parser

    args = build_parser().parse_args(["run", "hi"])
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        run_command, "load_cli_config_from_args", lambda _args: _run_config()
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs) or {"run_id": "trace-1", "final_text": "done"}
        ),
    )

    assert run_command.run_openminion(args) == 0
    assert captured["payload"]["session_id"] == "cli-run"


def test_run_openminion_json_output_single_process(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **_kwargs: {"run_id": "trace-123", "final_text": "done"},
    )

    code = run_command.run_openminion(_run_args(as_json=True))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "runtime_source": "inproc",
        "trace_id": "trace-123",
        "turn": {"run_id": "trace-123", "final_text": "done"},
    }


def test_run_openminion_plain_output_prefers_final_text(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **_kwargs: {"run_id": "trace-456", "final_text": "hello from run"},
    )

    code = run_command.run_openminion(_run_args())

    assert code == 0
    assert capsys.readouterr().out.strip() == "hello from run"


def test_run_openminion_jsonl_output_single_process(monkeypatch, capsys) -> None:
    args = _run_args()
    args.jsonl = True
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(),
    )
    monkeypatch.setattr(run_command, "resolve_default_agent_id", lambda _cfg: "agent")
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **_kwargs: {"run_id": "run-jsonl", "final_text": "done"},
    )

    assert run_command.run_openminion(args) == 0

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["event"] for event in events] == ["response", "done"]
    assert events[0]["data"]["turn"]["run_id"] == "run-jsonl"


def test_run_openminion_daemon_transport_outlives_configured_turn(
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(process_mode="daemon"),
    )
    monkeypatch.setattr(run_command, "resolve_default_agent_id", lambda _cfg: "agent")
    monkeypatch.setattr(
        run_command,
        "daemon_request",
        lambda **kwargs: (
            calls.append(dict(kwargs))
            or (200, {"ok": True, "turn": {"final_text": "done"}})
        ),
    )

    def _call_source(**kwargs):
        _status, payload = kwargs["daemon_call"](SimpleNamespace())
        return SimpleNamespace(payload=payload, source="daemon", fallback_reason="")

    monkeypatch.setattr(run_command, "call_daemon_or_inproc", _call_source)

    assert run_command.run_openminion(_run_args()) == 0

    assert calls[0]["timeout_s"] == 305
    assert capsys.readouterr().out.strip() == "done"


def test_run_openminion_suppresses_default_info_logs_and_restores_logging(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )

    def _run_turn(**_kwargs):
        logging.getLogger("openminion.test").info("internal setup detail")
        return {"run_id": "trace-quiet", "final_text": "quiet result"}

    monkeypatch.setattr(run_command, "run_turn", _run_turn)
    previous_disable_level = logging.root.manager.disable
    caplog.set_level(logging.INFO)

    code = run_command.run_openminion(_run_args())

    assert code == 0
    assert "internal setup detail" not in caplog.text
    assert logging.root.manager.disable == previous_disable_level


def test_run_openminion_honors_explicit_log_level(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENMINION_LOG_LEVEL", "INFO")
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )

    def _run_turn(**_kwargs):
        logging.getLogger("openminion.test").info("requested setup detail")
        return {"run_id": "trace-logs", "final_text": "logged result"}

    monkeypatch.setattr(run_command, "run_turn", _run_turn)
    caplog.set_level(logging.INFO)

    code = run_command.run_openminion(_run_args())

    assert code == 0
    assert "requested setup detail" in caplog.text


def test_run_openminion_marks_caller_handles_delivery_inproc(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        run_command, "load_cli_config_from_args", lambda _args: _run_config()
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs) or {"run_id": "trace-556", "final_text": "done"}
        ),
    )

    assert run_command.run_openminion(_run_args()) == 0

    inbound = captured["payload"]["inbound_metadata"]
    assert inbound["caller_handles_delivery"] == "true"


def test_run_openminion_marks_caller_handles_delivery_daemon(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(process_mode="daemon"),
    )
    monkeypatch.setattr(run_command, "resolve_default_agent_id", lambda _cfg: "agent")
    monkeypatch.setattr(
        run_command,
        "daemon_request",
        lambda **kwargs: (
            calls.append(dict(kwargs))
            or (200, {"ok": True, "turn": {"final_text": "done"}})
        ),
    )

    def _call_source(**kwargs):
        _status, payload = kwargs["daemon_call"](SimpleNamespace())
        return SimpleNamespace(payload=payload, source="daemon", fallback_reason="")

    monkeypatch.setattr(run_command, "call_daemon_or_inproc", _call_source)

    assert run_command.run_openminion(_run_args()) == 0

    meta = calls[0]["payload"]["meta"]
    assert meta["inbound_metadata"]["caller_handles_delivery"] == "true"


def test_run_openminion_tools_work_in_the_calling_directory(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        run_command, "load_cli_config_from_args", lambda _args: _run_config()
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs) or {"run_id": "trace-ws", "final_text": "done"}
        ),
    )

    assert run_command.run_openminion(_run_args()) == 0

    assert captured["payload"]["inbound_metadata"]["workspace_root"] == str(
        tmp_path.resolve()
    )


def test_run_openminion_dir_flag_sets_the_workspace_root(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}
    project = tmp_path / "project"
    project.mkdir()
    args = _run_args()
    args.dir = str(project)
    monkeypatch.setattr(
        run_command, "load_cli_config_from_args", lambda _args: _run_config()
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs) or {"run_id": "trace-dir", "final_text": "done"}
        ),
    )

    assert run_command.run_openminion(args) == 0

    assert captured["payload"]["inbound_metadata"]["workspace_root"] == str(
        project.resolve()
    )


def test_run_openminion_daemon_payload_carries_the_workspace_root(
    monkeypatch, tmp_path
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config(process_mode="daemon"),
    )
    monkeypatch.setattr(run_command, "resolve_default_agent_id", lambda _cfg: "agent")
    monkeypatch.setattr(
        run_command,
        "daemon_request",
        lambda **kwargs: (
            calls.append(dict(kwargs))
            or (200, {"ok": True, "turn": {"final_text": "done"}})
        ),
    )

    def _call_source(**kwargs):
        _status, payload = kwargs["daemon_call"](SimpleNamespace())
        return SimpleNamespace(payload=payload, source="daemon", fallback_reason="")

    monkeypatch.setattr(run_command, "call_daemon_or_inproc", _call_source)

    assert run_command.run_openminion(_run_args()) == 0

    meta = calls[0]["payload"]["meta"]
    assert meta["inbound_metadata"]["workspace_root"] == str(tmp_path.resolve())


def _run_config_with_workspace(workspace_root: str) -> SimpleNamespace:
    config = _run_config()
    config.runtime.tool_workspace_root = workspace_root
    return config


def test_run_openminion_preserves_the_configured_workspace_root(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, Any] = {}
    configured = tmp_path / "configured"
    configured.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config_with_workspace(str(configured)),
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs) or {"run_id": "trace-cfg", "final_text": "done"}
        ),
    )

    assert run_command.run_openminion(_run_args()) == 0

    # No caller override: nothing is sent, so the ingress keeps applying
    # runtime.tool_workspace_root.
    assert captured["payload"]["inbound_metadata"].get("workspace_root", "") == ""


def test_run_openminion_dir_flag_overrides_the_configured_workspace_root(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, Any] = {}
    configured = tmp_path / "configured"
    configured.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    args = _run_args()
    args.dir = str(explicit)
    monkeypatch.setattr(
        run_command,
        "load_cli_config_from_args",
        lambda _args: _run_config_with_workspace(str(configured)),
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs) or {"run_id": "trace-dir2", "final_text": "done"}
        ),
    )

    assert run_command.run_openminion(args) == 0

    assert captured["payload"]["inbound_metadata"]["workspace_root"] == str(
        explicit.resolve()
    )


def test_run_openminion_rejects_a_nonexistent_dir(monkeypatch, tmp_path) -> None:
    args = _run_args()
    args.dir = str(tmp_path / "missing")
    monkeypatch.setattr(
        run_command, "load_cli_config_from_args", lambda _args: _run_config()
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )

    with pytest.raises(RuntimeError, match="--dir is not a directory"):
        run_command.run_openminion(args)


def test_workspace_root_precedence_reaches_the_ingress(tmp_path) -> None:
    from openminion.services.runtime.ingress.requests import apply_workspace_root

    configured = tmp_path / "configured"
    configured.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()

    preserved = apply_workspace_root(
        inbound_metadata={"caller_handles_delivery": "true"},
        runtime_workspace_root=configured,
    )
    assert preserved is not None
    assert preserved["workspace_root"] == str(configured)

    overridden = apply_workspace_root(
        inbound_metadata={"workspace_root": str(caller)},
        runtime_workspace_root=configured,
    )
    assert overridden is not None
    assert overridden["workspace_root"] == str(caller)


def test_resolved_workspace_root_serves_relative_file_access(
    monkeypatch, tmp_path
) -> None:
    from openminion.tools.file.backends.local import LocalStorageBackend

    root = tmp_path / "workspace"
    root.mkdir()
    (root / "marker.txt").write_text("inside", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        run_command, "load_cli_config_from_args", lambda _args: _run_config()
    )
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )

    resolved = run_command._resolve_workspace_root(_run_args(), _run_config())
    assert resolved == str(root.resolve())

    backend = LocalStorageBackend(Path(resolved))
    assert backend.read("marker.txt").content == "inside"
    assert backend.find(str(Path(resolved)), pattern="*.txt").count == 1


def _capture_turn_inbound_metadata(
    monkeypatch, *, args: Namespace, config: SimpleNamespace, shape: str
) -> dict[str, str]:
    """Run the command with a mocked boundary and return its inbound metadata."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(run_command, "load_cli_config_from_args", lambda _args: config)
    monkeypatch.setattr(
        run_command, "resolve_default_agent_id", lambda _cfg: "default-agent"
    )
    if shape == "inproc":
        monkeypatch.setattr(
            run_command,
            "run_turn",
            lambda **kwargs: (
                captured.update(kwargs) or {"run_id": "trace-ws", "final_text": "done"}
            ),
        )
    else:
        monkeypatch.setattr(
            run_command,
            "daemon_request",
            lambda **kwargs: (
                captured.update(kwargs)
                or (200, {"ok": True, "turn": {"final_text": "done"}})
            ),
        )

        def _call_source(**kwargs):
            _status, payload = kwargs["daemon_call"](SimpleNamespace())
            return SimpleNamespace(payload=payload, source="daemon", fallback_reason="")

        monkeypatch.setattr(run_command, "call_daemon_or_inproc", _call_source)

    assert run_command.run_openminion(args) == 0
    payload = captured["payload"]
    if shape == "inproc":
        return dict(payload["inbound_metadata"])
    return dict(payload["meta"]["inbound_metadata"])


@pytest.mark.parametrize("shape", ["inproc", "daemon"])
@pytest.mark.parametrize("scenario", ["configured", "unset", "explicit-dir"])
def test_selected_root_reaches_file_tools_through_the_ingress(
    monkeypatch, tmp_path, shape: str, scenario: str
) -> None:
    """The command-selected root drives real file-tool access and its denial."""
    from openminion.modules.tool.runtime.context import RuntimeContext
    from openminion.modules.tool.runtime.policy import Policy
    from openminion.services.runtime.ingress.requests import apply_workspace_root
    from openminion.tools.file.plugin import _h_read_file

    roots = {}
    for name in ("configured", "caller", "explicit"):
        root = tmp_path / name
        root.mkdir()
        (root / "marker.txt").write_text(name, encoding="utf-8")
        roots[name] = root
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    monkeypatch.chdir(roots["caller"])

    config = _run_config()
    args = _run_args()
    if scenario == "configured":
        config.runtime.tool_workspace_root = str(roots["configured"])
    elif scenario == "explicit-dir":
        args.dir = str(roots["explicit"])

    inbound = _capture_turn_inbound_metadata(
        monkeypatch, args=args, config=config, shape=shape
    )
    configured_root = roots["configured"] if scenario == "configured" else None
    effective = apply_workspace_root(
        inbound_metadata=inbound, runtime_workspace_root=configured_root
    )
    assert effective is not None
    selected = Path(effective["workspace_root"])

    expected_root, marker = {
        "configured": (roots["configured"], "configured"),
        "unset": (roots["caller"], "caller"),
        "explicit-dir": (roots["explicit"], "explicit"),
    }[scenario]
    assert selected == expected_root.resolve()
    if scenario == "configured":
        # The command sends no override, so the ingress keeps the configured root.
        assert not inbound.get("workspace_root")

    ctx = RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(selected),
                "paths": {
                    "read_allow": [str(selected)],
                    "write_allow": [],
                    "deny": [],
                },
            }
        ),
        workspace=selected,
        run_root=tmp_path / "run",
        scope="WRITE_SAFE",
        confirm=False,
    )

    inside = _h_read_file({"path": "marker.txt"}, ctx)
    assert inside["ok"] is True
    assert inside["content"] == marker

    denied = _h_read_file({"path": str(outside)}, ctx)
    assert denied["ok"] is False
    assert denied["error"]["code"] == "POLICY_DENIED"


def test_run_openminion_loads_cli_config_from_args_unpatched(
    monkeypatch, tmp_path
) -> None:
    from openminion.base.config import OpenMinionConfig, save_config
    from openminion.cli.parser.base import build_parser
    from tests._csc_fixtures import _csc_install_default_agent

    home_dir = tmp_path / "home"
    home_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    custom_ws = tmp_path / "custom_ws"
    custom_ws.mkdir()

    config = OpenMinionConfig()
    _csc_install_default_agent(config, name="real-loaded-agent")
    config.runtime.process_mode = "single-process"
    config.runtime.tool_workspace_root = str(custom_ws)
    config_file = home_dir / "custom_config.json"
    save_config(config, str(config_file))

    parser = build_parser()
    args = parser.parse_args(
        [
            "--config",
            str(config_file),
            "--home-root",
            str(home_dir),
            "--data-root",
            str(data_dir),
            "run",
            "hello unpatched",
        ]
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **kwargs: (
            captured.update(kwargs)
            or {"run_id": "trace-unpatched", "final_text": "real done"}
        ),
    )

    code = run_command.run_openminion(args)
    assert code == 0
    assert captured["config_path"] == str(config_file)
    assert captured["payload"]["agent_id"] == "real-loaded-agent"
    assert captured["payload"]["inbound_metadata"].get("workspace_root", "") == ""
