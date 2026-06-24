from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = (
    REPO_ROOT
    / "openminion"
    / "scripts"
    / "validate"
    / "focus"
    / "terminal_no_textual.py"
)


@pytest.fixture(scope="module")
def guard_module():
    spec = importlib.util.spec_from_file_location("ftf_no_textual_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ftf_no_textual_guard"] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fixture.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_detects_bare_import_textual(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path, "import textual\n")
    violations = guard_module._scan_file(fixture)
    assert len(violations) == 1
    assert violations[0].module == "textual"


def test_detects_import_textual_submodule(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path, "import textual.widgets\n")
    violations = guard_module._scan_file(fixture)
    assert len(violations) == 1
    assert violations[0].module == "textual.widgets"


def test_detects_from_textual_import(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(tmp_path, "from textual import App\n")
    violations = guard_module._scan_file(fixture)
    assert len(violations) == 1
    assert violations[0].module == "textual"
    assert violations[0].symbol == "App"


def test_detects_from_textual_submodule_import(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from textual.app import App\nfrom textual.widgets import Static, Label\n",
    )
    violations = guard_module._scan_file(fixture)
    symbols = {(v.module, v.symbol) for v in violations}
    assert ("textual.app", "App") in symbols
    assert ("textual.widgets", "Static") in symbols
    assert ("textual.widgets", "Label") in symbols


def test_does_not_flag_legitimate_imports(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from rich.console import Console\n"
        "from prompt_toolkit import PromptSession\n"
        "import asyncio\n"
        "from openminion.api.runtime import APIRuntime\n",
    )
    assert guard_module._scan_file(fixture) == []


def test_live_tree_baseline_is_empty(guard_module) -> None:
    violations = guard_module._scan_terminal_surface()
    assert violations == [], (
        f"terminal/ must not import textual; found: "
        f"{[v.as_baseline_line() for v in violations]}"
    )


def test_main_passes_on_empty_baseline(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dir = tmp_path / "terminal"  # does not exist
    fake_baseline = tmp_path / "baseline.txt"  # does not exist

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "FOCUS_TERMINAL_DIR", fake_dir)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", fake_baseline)

    assert guard_module.main([]) == 0


def test_main_fails_on_new_violation(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dir = tmp_path / "terminal"
    fake_dir.mkdir()
    (fake_dir / "shell.py").write_text(
        "from textual.app import App\n", encoding="utf-8"
    )
    fake_baseline = tmp_path / "baseline.txt"  # empty/missing

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "FOCUS_TERMINAL_DIR", fake_dir)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", fake_baseline)

    assert guard_module.main([]) == 1
