from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.cli.main import main as cli_main


# ── No-subcommand: configured + TTY → focus ─────────────────────────


def _stub_route(
    *,
    should_launch_setup: bool = False,
    should_fail_fast: bool = False,
):
    return SimpleNamespace(
        status="ok",
        should_launch_setup=should_launch_setup,
        should_fail_fast=should_fail_fast,
    )


def test_no_subcommand_with_tty_and_config_launches_focus(monkeypatch) -> None:
    called = {}

    def _fake_run_focus(args):
        called["focus"] = True
        return 0

    monkeypatch.setattr("openminion.cli.commands.focus.run_focus", _fake_run_focus)
    monkeypatch.setattr(
        "openminion.cli.main.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    rc = cli_main([])
    assert rc == 0
    assert called.get("focus") is True


def test_no_subcommand_with_no_config_runs_setup(monkeypatch) -> None:
    called = {}

    def _fake_run_setup(_args):
        called["setup"] = True
        return 0

    monkeypatch.setattr("openminion.cli.commands.setup.run_setup", _fake_run_setup)
    monkeypatch.setattr(
        "openminion.cli.main.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(should_launch_setup=True),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    rc = cli_main([])
    assert rc == 0
    assert called.get("setup") is True


def test_no_subcommand_with_should_fail_fast_exits_two(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "openminion.cli.main.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(should_fail_fast=True),
    )
    monkeypatch.setattr(
        "openminion.cli.main.format_fail_fast_message",
        lambda **kw: "remediation message",
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    with pytest.raises(SystemExit) as excinfo:
        cli_main([])
    assert excinfo.value.code == 2


def test_no_subcommand_pipe_with_data_dispatches_terminal_flow(
    monkeypatch, capsys
) -> None:
    import io
    import sys as _sys

    monkeypatch.setattr(
        "openminion.cli.main.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    # Non-TTY stdin with content.
    fake_stdin = io.StringIO("summarize the plan\n")
    fake_stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(_sys, "stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    called = {}

    def _fake_run_focus(_args):
        called["focus"] = True
        return 0

    monkeypatch.setattr("openminion.cli.commands.focus.run_focus", _fake_run_focus)

    rc = cli_main([])
    assert rc == 0
    assert called.get("focus") is True


def test_no_subcommand_pipe_with_no_data_prints_help(monkeypatch, capsys) -> None:
    import io
    import sys as _sys

    monkeypatch.setattr(
        "openminion.cli.main.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    fake_stdin = io.StringIO("")
    fake_stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(_sys, "stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    called = {}

    def _fake_run_focus(_args):
        called["focus"] = True
        return 0

    monkeypatch.setattr("openminion.cli.commands.focus.run_focus", _fake_run_focus)

    rc = cli_main([])
    assert rc == 1
    assert "focus" not in called, (
        "empty stdin must NOT dispatch focus; print_help fallback applies"
    )


def test_no_subcommand_pipe_with_whitespace_only_data_prints_help(
    monkeypatch, capsys
) -> None:
    import io
    import sys as _sys

    monkeypatch.setattr(
        "openminion.cli.main.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    fake_stdin = io.StringIO("   \n  \t\n")
    fake_stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(_sys, "stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    called = {}

    def _fake_run_focus(_args):
        called["focus"] = True
        return 0

    monkeypatch.setattr("openminion.cli.commands.focus.run_focus", _fake_run_focus)

    rc = cli_main([])
    assert rc == 1
    assert "focus" not in called


# ── Subcommand handlers ──────────────────────────────────────────────


def test_explicit_focus_subcommand_routes_to_focus(monkeypatch) -> None:
    called = {}

    def _fake(_args):
        called["focus"] = True
        return 0

    monkeypatch.setattr("openminion.cli.commands.focus.run_focus", _fake)
    rc = cli_main(["focus"])
    assert rc == 0
    assert called.get("focus") is True


def test_run_tui_first_statement_is_deprecation_print() -> None:
    import inspect

    from openminion.cli.commands.tui import run_tui

    src = inspect.getsource(run_tui)
    # The body must contain the deprecation print before any dashboard
    # launch path is invoked. Establish ordering by string position.
    dep_pos = src.find("[deprecated] 'openminion tui'")
    dashboard_launch_pos = src.find("launch_dashboard(")
    assert dep_pos != -1, "run_tui missing deprecation notice"
    assert dashboard_launch_pos != -1, "run_tui missing dashboard launch"
    assert dep_pos < dashboard_launch_pos, (
        "deprecation print must precede dashboard launch so the user "
        "always sees the notice even when the mount itself fails"
    )


def test_chat_subcommand_routes_unchanged(monkeypatch) -> None:
    called = {}

    def _fake(_args, _app=None):
        called["chat"] = True
        return 0

    # Chat handler may need APIRuntime; skip the heavy path by patching.
    monkeypatch.setattr("openminion.cli.commands.chat.run_chat", _fake)
    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        classmethod(lambda cls, *a, **kw: object()),
    )
    rc = cli_main(["chat"])
    assert rc == 0
    assert called.get("chat") is True


# ── Live tui.py deprecation source check ─────────────────────────────


def test_run_tui_source_contains_deprecation_print() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "cli"
        / "commands"
        / "tui.py"
    ).read_text(encoding="utf-8")
    assert "[deprecated] 'openminion tui'" in src
