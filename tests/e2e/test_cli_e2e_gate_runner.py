from __future__ import annotations

import pytest


import importlib.util
from pathlib import Path
import subprocess
from types import ModuleType

pytestmark = pytest.mark.e2e


_RUNNER_PATH = (
    Path(__file__).resolve().parents[1] / "e2e" / "runners" / "run_cli_e2e_gate.py"
)


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_cli_e2e_gate", _RUNNER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_timeout_seconds_defaults_for_missing_or_invalid_values() -> None:
    runner = _load_runner()

    assert runner._timeout_seconds({}) == runner.DEFAULT_LIVE_TIMEOUT_SECONDS
    assert (
        runner._timeout_seconds({runner.TIMEOUT_ENV: "abc"})
        == runner.DEFAULT_LIVE_TIMEOUT_SECONDS
    )
    assert (
        runner._timeout_seconds({runner.TIMEOUT_ENV: "0"})
        == runner.DEFAULT_LIVE_TIMEOUT_SECONDS
    )


def test_timeout_seconds_uses_positive_override() -> None:
    runner = _load_runner()

    assert runner._timeout_seconds({runner.TIMEOUT_ENV: "45"}) == 45


def test_run_returns_124_when_subprocess_times_out(monkeypatch, capsys) -> None:
    runner = _load_runner()

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="demo", timeout=kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "call", _raise_timeout)

    result = runner._run(["demo"], env={}, timeout_seconds=3)

    assert result == 124
    assert "timed out after 3s" in capsys.readouterr().err
