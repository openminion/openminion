"""`daemon stop` may only signal a process it can prove owns this daemon."""

from __future__ import annotations

from pathlib import Path
import sys

import psutil
import pytest

import openminion.daemon as daemon_mod
from openminion.cli.commands import daemon as daemon_cmd
from openminion.cli.transport.daemon_client import DaemonEndpoint

CONFIG_PATH = "/srv/openminion/agents.json"
HOST = "127.0.0.1"
PORT = 18789


class _FakeProcess:
    def __init__(
        self, argv: list[str], exe: str | None, *, alive: dict[str, bool]
    ) -> None:
        self._argv = argv
        self._exe = exe
        self._alive = alive
        self.terminated = 0
        self.killed = 0

    def cmdline(self) -> list[str]:
        return list(self._argv)

    def exe(self) -> str:
        if self._exe is None:
            raise psutil.AccessDenied(pid=4242)
        return self._exe

    def terminate(self) -> None:
        self.terminated += 1
        self._alive["alive"] = False

    def kill(self) -> None:
        self.killed += 1
        self._alive["alive"] = False

    def is_running(self) -> bool:
        return bool(self._alive["alive"])


def _serve_argv(
    pid_file: Path,
    *,
    config: str = CONFIG_PATH,
    host: str = HOST,
    port: int = PORT,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "openminion.daemon",
        "serve",
        "--config",
        config,
        "--host",
        host,
        "--port",
        str(port),
        "--pid-file",
        str(pid_file),
    ]


@pytest.fixture()
def stop_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pid_file = tmp_path / "state" / "openminiond.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("4242\n", encoding="utf-8")
    state = {"alive": True, "pid_reads": [4242, 4242], "process": None}

    monkeypatch.setattr(
        daemon_cmd,
        "resolve_daemon_endpoint",
        lambda *a, **k: DaemonEndpoint(config_path=CONFIG_PATH, host=HOST, port=PORT),
    )
    monkeypatch.setattr(daemon_cmd, "load_config", lambda path: object())
    monkeypatch.setattr(daemon_mod, "resolve_daemon_pid_file", lambda *a, **k: pid_file)
    monkeypatch.setattr(
        daemon_mod,
        "read_pid",
        lambda path: state["pid_reads"].pop(0) if state["pid_reads"] else 4242,
    )
    monkeypatch.setattr(daemon_mod, "process_alive", lambda pid: state["alive"])
    monkeypatch.setattr(daemon_cmd.time, "sleep", lambda seconds: None)

    def _process(pid: int):
        return state["process"]

    monkeypatch.setattr(daemon_cmd.psutil, "Process", _process)
    return pid_file, state


def _install_process(state: dict, argv: list[str], exe: str = sys.executable):
    process = _FakeProcess(argv, exe, alive=state)
    state["process"] = process
    return process


def test_matching_identity_stops_the_daemon(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file))

    assert daemon_cmd.daemon_stop(None) == 0
    assert process.terminated == 1
    assert "Stopped daemon pid=4242" in capsys.readouterr().out


def test_virtual_environment_base_interpreter_stops_the_daemon(
    stop_env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    venv_python = tmp_path / "venv" / "Scripts" / "python.exe"
    base_python = tmp_path / "base" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    base_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    base_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(daemon_cmd.sys, "executable", str(venv_python))
    monkeypatch.setattr(
        daemon_cmd.sys, "_base_executable", str(base_python), raising=False
    )
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file), exe=str(base_python))

    assert daemon_cmd.daemon_stop(None) == 0
    assert process.terminated == 1
    assert "Stopped daemon pid=4242" in capsys.readouterr().out


def test_different_config_is_refused_before_any_signal(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    process = _install_process(
        state, _serve_argv(pid_file, config="/srv/other/agents.json")
    )

    assert daemon_cmd.daemon_stop(None) == 1
    out = capsys.readouterr().out
    assert "--config" in out and "/srv/other/agents.json" in out
    assert "No process was signalled" in out
    assert process.terminated == 0


def test_missing_pid_file_argument_is_refused(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    _pid_file, state = stop_env
    argv = [sys.executable, "-m", "openminion.daemon", "serve", "--config", CONFIG_PATH]
    process = _install_process(state, argv)

    assert daemon_cmd.daemon_stop(None) == 1
    assert "missing --host, --port, --pid-file" in capsys.readouterr().out
    assert process.terminated == 0


def test_duplicate_identity_field_is_refused(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    argv = _serve_argv(pid_file) + ["--config", "/srv/openminion/agents.json"]
    process = _install_process(state, argv)

    assert daemon_cmd.daemon_stop(None) == 1
    assert "duplicate --config" in capsys.readouterr().out
    assert process.terminated == 0


def test_unrelated_command_line_is_refused(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    _pid_file, state = stop_env
    process = _install_process(state, ["/usr/bin/vim", "notes.txt"])

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match this daemon's launch form" in capsys.readouterr().out
    assert process.terminated == 0


def test_different_host_or_port_is_refused(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file, port=19001))

    assert daemon_cmd.daemon_stop(None) == 1
    assert "19001" in capsys.readouterr().out
    assert process.terminated == 0


def test_replaced_pid_file_is_refused_at_the_final_reread(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    state["pid_reads"] = [4242, 5150]
    process = _install_process(state, _serve_argv(pid_file))

    assert daemon_cmd.daemon_stop(None) == 1
    assert "changed while verifying PID 4242" in capsys.readouterr().out
    assert process.terminated == 0


def test_dead_pid_still_cleans_the_stale_file(
    stop_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    state["alive"] = False
    monkeypatch.setattr(
        daemon_cmd.psutil,
        "Process",
        lambda pid: pytest.fail("must not inspect a dead PID"),
    )

    assert daemon_cmd.daemon_stop(None) == 0
    assert "Removed stale daemon PID file." in capsys.readouterr().out
    assert pid_file.exists() is False


def test_unreadable_executable_refuses_before_signalling(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file), exe=None)

    assert daemon_cmd.daemon_stop(None) == 1
    assert "executable identity could not be read" in capsys.readouterr().out
    assert process.terminated == 0


def test_python_prefixed_but_unrelated_binary_is_refused(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    process = _install_process(
        state, _serve_argv(pid_file), exe="/tmp/python-unrelated"
    )

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match the interpreter" in capsys.readouterr().out
    assert process.terminated == 0


def _framework_paths(root: Path, version: str) -> tuple[Path, Path]:
    install = root / "Versions" / version / "bin" / "python3"
    launcher = (
        root
        / "Versions"
        / version
        / "Resources"
        / "Python.app"
        / "Contents"
        / "MacOS"
        / "Python"
    )
    install.parent.mkdir(parents=True, exist_ok=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    install.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    return install, launcher


def test_platform_python_launcher_of_this_installation_is_accepted(
    stop_env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The framework launcher is trusted only inside the interpreter's own tree."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    install, launcher = _framework_paths(tmp_path / "Python.framework", version)
    monkeypatch.setattr(daemon_cmd.sys, "executable", str(install))
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file), exe=str(launcher))

    assert daemon_cmd.daemon_stop(None) == 0
    assert process.terminated == 1
    assert "Stopped daemon pid=4242" in capsys.readouterr().out


def test_a_nested_foreign_launcher_is_refused(
    stop_env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A path that only shares the version prefix and the suffix is not this launcher."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    root = tmp_path / "Python.framework"
    install = root / "Versions" / version / "bin" / "python3"
    foreign = (
        root
        / "Versions"
        / version
        / "foreign"
        / "Versions"
        / version
        / "Resources"
        / "Python.app"
        / "Contents"
        / "MacOS"
        / "Python"
    )
    install.parent.mkdir(parents=True)
    foreign.parent.mkdir(parents=True)
    install.write_text("#!/bin/sh\n", encoding="utf-8")
    foreign.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(daemon_cmd.sys, "executable", str(install))
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file), exe=str(foreign))

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match the interpreter" in capsys.readouterr().out
    assert process.terminated == 0
    assert pid_file.exists() is True


def test_a_launcher_sharing_only_an_ancestor_is_refused(
    stop_env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Sharing a parent directory is not installation identity."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    root = tmp_path / "Python.framework"
    install = root / "bin" / "python3"
    launcher = (
        root
        / "Versions"
        / version
        / "Resources"
        / "Python.app"
        / "Contents"
        / "MacOS"
        / "Python"
    )
    install.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    install.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(daemon_cmd.sys, "executable", str(install))
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file), exe=str(launcher))

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match the interpreter" in capsys.readouterr().out
    assert process.terminated == 0


def test_same_version_launcher_from_another_installation_is_refused(
    stop_env, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A matching version and shape elsewhere is not this interpreter."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    _install, launcher = _framework_paths(tmp_path / "Python.framework", version)
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file), exe=str(launcher))

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match the interpreter" in capsys.readouterr().out
    assert process.terminated == 0


def test_framework_launcher_for_another_version_is_refused(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """The launcher form is only trusted for the version this command runs."""
    pid_file, state = stop_env
    other = 99 if sys.version_info.minor != 99 else 98
    process = _install_process(
        state,
        _serve_argv(pid_file),
        exe=(
            "/opt/homebrew/Frameworks/Python.framework/Versions/"
            f"{sys.version_info.major}.{other}/Python.app/Contents/MacOS/Python"
        ),
    )

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match the interpreter" in capsys.readouterr().out
    assert process.terminated == 0


def test_a_python_basename_alone_does_not_prove_identity(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """`/system/python3` and `/unrelated/python3` are different binaries."""
    pid_file, state = stop_env
    argv = _serve_argv(pid_file)
    argv[0] = "/system/python3"
    process = _install_process(state, argv, exe="/unrelated/python3")

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match the interpreter" in capsys.readouterr().out
    assert process.terminated == 0


def test_a_shared_unrelated_path_does_not_prove_identity(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """Matching argv[0] and exe prove nothing when both name a foreign launcher."""
    pid_file, state = stop_env
    argv = _serve_argv(pid_file)
    argv[0] = "/unrelated/python-pretend"
    process = _install_process(state, argv, exe="/unrelated/python-pretend")

    assert daemon_cmd.daemon_stop(None) == 1
    assert "does not match the interpreter" in capsys.readouterr().out
    assert process.terminated == 0


def test_reachable_endpoint_for_another_config_is_refused_before_signalling(
    stop_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A listener answering for a different config is not this daemon."""
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file))
    monkeypatch.setattr(
        daemon_cmd,
        "probe_daemon_endpoint",
        lambda endpoint: (
            "mismatch",
            {"daemon": {"config_path": "/elsewhere/agents.json"}},
        ),
    )

    assert daemon_cmd.daemon_stop(None) == 1
    out = capsys.readouterr().out
    assert "/elsewhere/agents.json" in out
    assert "No process was signalled" in out
    assert process.terminated == 0


def test_reachable_endpoint_for_the_same_config_still_stops(
    stop_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The endpoint check only refuses a contradiction, not a healthy daemon."""
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file))
    monkeypatch.setattr(
        daemon_cmd,
        "probe_daemon_endpoint",
        lambda endpoint: ("ok", {"daemon": {"config_path": CONFIG_PATH}}),
    )

    assert daemon_cmd.daemon_stop(None) == 0
    assert process.terminated == 1
    assert "Stopped daemon pid=4242" in capsys.readouterr().out


def test_unreachable_endpoint_does_not_block_a_stop(
    stop_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No listener means the PID checks decide, as before."""
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file))
    monkeypatch.setattr(
        daemon_cmd, "probe_daemon_endpoint", lambda endpoint: ("unreachable", {})
    )

    assert daemon_cmd.daemon_stop(None) == 0
    assert process.terminated == 1
    assert "Stopped daemon pid=4242" in capsys.readouterr().out


def test_stale_cleanup_reports_the_replacement_it_left_behind(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    """The message must describe the cleanup that happened, not the intended one."""
    pid_file, state = stop_env
    state["alive"] = False
    pid_file.write_text("5150\n", encoding="utf-8")

    assert daemon_cmd.daemon_stop(None) == 0
    out = capsys.readouterr().out
    assert "left in place" in out
    assert "Removed stale daemon PID file." not in out
    assert pid_file.read_text(encoding="utf-8").strip() == "5150"


def test_relative_launch_path_is_not_reanchored_on_the_caller_cwd(
    stop_env, capsys: pytest.CaptureFixture[str]
) -> None:
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file, config="agents.json"))

    assert daemon_cmd.daemon_stop(None) == 1
    assert "--config" in capsys.readouterr().out
    assert process.terminated == 0


def test_replacement_written_before_cleanup_survives(
    stop_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cleanup must not delete a PID file that now names a different daemon."""
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file))

    def _terminate() -> None:
        process.terminated += 1
        # The replacement lands between the signal and the cleanup read.
        pid_file.write_text("5150\n", encoding="utf-8")
        state["alive"] = False

    monkeypatch.setattr(process, "terminate", _terminate)

    assert daemon_cmd.daemon_stop(None) == 0
    assert pid_file.read_text(encoding="utf-8").strip() == "5150"
    assert "Stopped daemon pid=4242" in capsys.readouterr().out


def test_process_that_exited_after_the_reread_is_not_signalled(
    stop_env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same instance is asked whether it is still running before it is signalled."""
    pid_file, state = stop_env
    process = _install_process(state, _serve_argv(pid_file))
    monkeypatch.setattr(daemon_mod, "process_alive", lambda pid: True)
    monkeypatch.setattr(process, "is_running", lambda: False)

    assert daemon_cmd.daemon_stop(None) == 0
    assert process.terminated == 0
    assert "exited before it could be signalled" in capsys.readouterr().out
    assert not pid_file.exists()
