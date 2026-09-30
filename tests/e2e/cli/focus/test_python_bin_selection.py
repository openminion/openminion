from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.e2e.cli.focus.conftest import _resolve_python_bin

pytestmark = pytest.mark.e2e


def test_python_bin_prefers_explicit_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENMINION_PYTHON", "/opt/openminion/python")

    assert _resolve_python_bin(tmp_path) == Path("/opt/openminion/python")


def test_python_bin_uses_checkout_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENMINION_PYTHON", raising=False)
    local = tmp_path / ".venv" / "bin" / "python3.11"
    local.parent.mkdir(parents=True)
    local.touch()

    assert _resolve_python_bin(tmp_path) == local


def test_python_bin_falls_back_to_running_interpreter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENMINION_PYTHON", raising=False)

    assert _resolve_python_bin(tmp_path) == Path(sys.executable)
