from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = REPO_ROOT / "openminion" / "scripts" / "manual" / "context_reset.py"
PY = sys.executable


@pytest.fixture(scope="module")
def guard_module():
    spec = importlib.util.spec_from_file_location("ccs_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["ccs_guard"] = module
    spec.loader.exec_module(module)
    return module


def test_guard_passes_on_current_baseline():
    result = subprocess.run(
        [PY, str(GUARD_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"guard failed on baseline: stdout={result.stdout} stderr={result.stderr}"
    )


def test_guard_flags_reintroduced_symbol(guard_module, tmp_path, monkeypatch):
    fake_pkg = tmp_path / "src" / "openminion" / "fake_module"
    fake_pkg.mkdir(parents=True)
    (fake_pkg / "__init__.py").write_text("")
    target = fake_pkg / "regression.py"
    target.write_text(
        "from typing import Any\n"
        "\n"
        "class BudgetReport:\n"  # retired symbol — must be flagged
        "    pass\n"
        "\n"
        "def get_slice_v15(scope: str) -> Any:\n"  # retired symbol
        "    return scope\n"
    )
    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "SCAN_ROOT", tmp_path / "src" / "openminion")
    findings = guard_module._scan_file(target)
    flagged_symbols = {symbol for _, symbol, _, _ in findings}
    assert "BudgetReport" in flagged_symbols
    assert "get_slice_v15" in flagged_symbols


def test_guard_does_not_flag_docstring_or_comment_mentions(
    guard_module, tmp_path, monkeypatch
):
    fake_pkg = tmp_path / "src" / "openminion" / "fake_module2"
    fake_pkg.mkdir(parents=True)
    (fake_pkg / "__init__.py").write_text("")
    target = fake_pkg / "innocent.py"
    target.write_text(
        '"""Module that mentions BudgetReport and get_slice_v15 in a docstring.\n'
        "\n"
        "Historical note: these symbols were retired by CONTRACT_RESET_2026.\n"
        '"""\n'
        "\n"
        "# Comment mentioning ContextDraft and ContextPackPolicy — historical only.\n"
        "ERR_MSG = 'BudgetReport was removed in CCS-05; use TokenBudgetReport.'\n"
    )
    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "SCAN_ROOT", tmp_path / "src" / "openminion")
    findings = guard_module._scan_file(target)
    assert findings == [], (
        f"docstring/comment/string-literal mentions should not be flagged; got: {findings}"
    )


def test_guard_does_not_flag_partial_word_match(guard_module, tmp_path, monkeypatch):
    fake_pkg = tmp_path / "src" / "openminion" / "fake_module3"
    fake_pkg.mkdir(parents=True)
    (fake_pkg / "__init__.py").write_text("")
    target = fake_pkg / "uses_replacement.py"
    target.write_text(
        "class TokenBudgetReport:\n"
        "    pass\n"
        "\n"
        "def get_slice(scope: str) -> str:\n"
        "    return scope\n"
    )
    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "SCAN_ROOT", tmp_path / "src" / "openminion")
    findings = guard_module._scan_file(target)
    assert findings == [], (
        f"replacement names must not collide with retired-symbol names; got: {findings}"
    )
