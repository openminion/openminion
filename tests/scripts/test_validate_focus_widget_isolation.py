from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_PATH = (
    REPO_ROOT / "openminion" / "scripts" / "validate" / "focus" / "widget_isolation.py"
)


@pytest.fixture(scope="module")
def guard_module():
    spec = importlib.util.spec_from_file_location("fns_isolation_guard", GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fns_isolation_guard"] = module
    spec.loader.exec_module(module)
    return module


def _write_fixture(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "fixture.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_detects_package_re_export_form(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from openminion.cli.tui.widgets import ChatView, ChatInputBar\n",
    )
    violations = guard_module._scan_file(fixture)
    symbols = {v.symbol for v in violations}
    assert symbols == {"ChatView", "ChatInputBar"}


def test_detects_submodule_absolute_form(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from openminion.cli.tui.widgets.chat import ChatView, MessageWidget\n"
        "from openminion.cli.tui.widgets.input_bar import ChatInputBar\n",
    )
    violations = guard_module._scan_file(fixture)
    symbols = {v.symbol for v in violations}
    assert symbols == {"ChatView", "MessageWidget", "ChatInputBar"}


def test_detects_relative_form(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from ..widgets import ChatView\n"
        "from ...widgets.input_bar import ChatInputBar\n",
    )
    violations = guard_module._scan_file(fixture)
    symbols = {v.symbol for v in violations}
    assert symbols == {"ChatView", "ChatInputBar"}


def test_detects_multiline_parenthesized_import(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from openminion.cli.tui.widgets import (\n"
        "    ChatInputBar,\n"
        "    ChatSearchBar,\n"
        "    ChatView,\n"
        ")\n",
    )
    violations = guard_module._scan_file(fixture)
    symbols = {v.symbol for v in violations}
    # ChatSearchBar is on the documented exception list and MUST NOT
    # appear in violations.
    assert symbols == {"ChatView", "ChatInputBar"}


def test_exception_list_symbols_do_not_trigger(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from openminion.cli.tui.widgets import (\n"
        "    ChatSearchBar,\n"
        "    ToolBlockWidget,\n"
        "    ToolApprovalWidget,\n"
        "    SlashCommandOverlay,\n"
        "    FileMentionOverlay,\n"
        "    ThinkingIndicator,\n"
        ")\n",
    )
    violations = guard_module._scan_file(fixture)
    assert violations == []


def test_clean_file_returns_no_violations(guard_module, tmp_path: Path) -> None:
    fixture = _write_fixture(
        tmp_path,
        "from textual.widget import Widget\n"
        "from openminion.cli.tui.focus.widgets import FocusStatusLine\n",
    )
    assert guard_module._scan_file(fixture) == []


def test_baseline_grandfathers_known_violations(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a tiny fake focus tree the validator scans.
    fake_screen = tmp_path / "screen.py"
    fake_screen.write_text(
        "from openminion.cli.tui.widgets import ChatView, ChatInputBar\n",
        encoding="utf-8",
    )
    fake_widgets = tmp_path / "widgets"
    fake_widgets.mkdir()
    (fake_widgets / "__init__.py").write_text("", encoding="utf-8")

    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        f"{fake_screen.relative_to(tmp_path).as_posix()}:1:ChatView\n"
        f"{fake_screen.relative_to(tmp_path).as_posix()}:1:ChatInputBar\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "FOCUS_SCREEN", fake_screen)
    monkeypatch.setattr(guard_module, "FOCUS_WIDGETS_DIR", fake_widgets)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", baseline)

    # Subset (== baseline) → exit 0.
    assert guard_module.main([]) == 0


def test_new_violation_outside_baseline_fails(
    guard_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_screen = tmp_path / "screen.py"
    fake_screen.write_text(
        "from openminion.cli.tui.widgets import ChatView, ChatInputBar\n",
        encoding="utf-8",
    )
    fake_widgets = tmp_path / "widgets"
    fake_widgets.mkdir()
    (fake_widgets / "__init__.py").write_text("", encoding="utf-8")

    # Baseline allows ChatView only — ChatInputBar is a new violation.
    baseline = tmp_path / "baseline.txt"
    baseline.write_text(
        f"{fake_screen.relative_to(tmp_path).as_posix()}:1:ChatView\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(guard_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(guard_module, "FOCUS_SCREEN", fake_screen)
    monkeypatch.setattr(guard_module, "FOCUS_WIDGETS_DIR", fake_widgets)
    monkeypatch.setattr(guard_module, "BASELINE_PATH", baseline)

    assert guard_module.main([]) == 1


def test_live_tree_baseline_matches_expected_violations(
    guard_module,
) -> None:
    violations = guard_module._scan_focus_surface()
    symbols_by_file: dict[str, set[str]] = {}
    for v in violations:
        symbols_by_file.setdefault(v.file, set()).add(v.symbol)

    # The only file with violations today is focus/screen.py; both
    # symbols are body widgets. ChatSearchBar (also imported there)
    # is on the exception list and must NOT show up.
    expected_file = "src/openminion/cli/tui/focus/screen.py"
    assert set(symbols_by_file.keys()) <= {expected_file}, (
        f"unexpected violation file(s): {set(symbols_by_file.keys())}"
    )
    if expected_file in symbols_by_file:
        assert symbols_by_file[expected_file] == {"ChatView", "ChatInputBar"}, (
            f"baseline drift: {symbols_by_file[expected_file]}; "
            "FNS-04 may have already landed (in which case this test should "
            "flip to assert empty violations)."
        )
