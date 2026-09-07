from __future__ import annotations

import pytest


import importlib.util
from pathlib import Path
import subprocess
import sys
from types import ModuleType

pytestmark = pytest.mark.e2e


_RUNNER_PATH = (
    Path(__file__).resolve().parents[1] / "e2e" / "runners" / "run_cli_focus_e2e.py"
)


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_cli_focus_e2e", _RUNNER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_timeout_uses_override() -> None:
    runner = _load_runner()
    suite = runner.SUITES["tools"]

    assert runner._runner_timeout_seconds({runner._TIMEOUT_ENV: "12"}, suite) == 12


def test_runner_timeout_defaults_for_live_and_complex_suites() -> None:
    runner = _load_runner()

    assert runner._runner_timeout_seconds({}, runner.SUITES["tools"]) == 1500
    assert runner._runner_timeout_seconds({}, runner.SUITES["complex"]) == 4200
    assert runner._runner_timeout_seconds({}, runner.SUITES["local"]) is None


def test_run_returns_124_when_subprocess_times_out(monkeypatch, capsys) -> None:
    runner = _load_runner()

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="demo", timeout=kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "call", _raise_timeout)

    result = runner._run(("tests/example.py",), env={}, timeout_seconds=3)

    assert result == 124
    assert "timed out after 3s" in capsys.readouterr().err


def test_run_uses_the_active_python_interpreter(monkeypatch) -> None:
    runner = _load_runner()
    captured: dict[str, object] = {}

    def _capture(command, **_kwargs):
        captured["command"] = command
        return 0

    monkeypatch.setattr(runner.subprocess, "call", _capture)

    assert runner._run(("tests/example.py",), env={}) == 0
    assert captured["command"][0] == sys.executable


def test_main_exports_the_active_interpreter_for_pty_children(monkeypatch) -> None:
    runner = _load_runner()
    captured: dict[str, object] = {}
    monkeypatch.setenv("OPENMINION_PYTHON", "stale-python")

    def _capture(_paths, *, env, **_kwargs):
        captured["env"] = env
        return 0

    monkeypatch.setattr(runner, "_run", _capture)

    assert runner.main(["onboarding"]) == 0
    assert captured["env"]["OPENMINION_PYTHON"] == sys.executable
