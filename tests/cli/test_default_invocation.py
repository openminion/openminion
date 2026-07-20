from __future__ import annotations

from pathlib import Path
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


def test_no_subcommand_with_tty_and_config_launches_interactive(monkeypatch) -> None:
    called = {}

    def _fake_run_interactive(args):
        called["interactive"] = args
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.interactive.run_interactive",
        _fake_run_interactive,
    )
    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    rc = cli_main([])
    assert rc == 0
    interactive_args = called.get("interactive")
    assert interactive_args is not None
    assert interactive_args.rich is False


def test_no_subcommand_can_opt_into_rich_interactive(monkeypatch) -> None:
    called = {}

    def _fake_run_interactive(args):
        called["interactive"] = args
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.interactive.run_interactive",
        _fake_run_interactive,
    )
    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert cli_main(["--rich"]) == 0
    interactive_args = called.get("interactive")
    assert interactive_args is not None
    assert interactive_args.rich is True


def test_no_subcommand_forwards_canonical_interactive_options(monkeypatch) -> None:
    called = {}

    def _fake_run_interactive(args):
        called["interactive"] = args
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.interactive.run_interactive",
        _fake_run_interactive,
    )
    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert (
        cli_main(
            [
                "--agent",
                "demo-agent",
                "--session",
                "demo-session",
                "--dir",
                "/tmp/demo-workspace",
                "--theme",
                "light",
                "--no-context",
                "--no-update-check",
                "--verbosity",
                "quiet",
            ]
        )
        == 0
    )
    interactive_args = called["interactive"]
    assert interactive_args.agent == "demo-agent"
    assert interactive_args.session == "demo-session"
    assert interactive_args.dir == "/tmp/demo-workspace"
    assert interactive_args.theme == "light"
    assert interactive_args.no_context is True
    assert interactive_args.no_update_check is True
    assert interactive_args.verbosity == "quiet"


def test_no_subcommand_demo_requests_demo_onboarding(monkeypatch) -> None:
    requested_modes = []

    def _fake_route(**kwargs):
        requested_modes.append(kwargs["requested_mode"])
        return _stub_route()

    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        _fake_route,
    )
    monkeypatch.setattr(
        "openminion.cli.commands.interactive.run_interactive", lambda _args: 0
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    assert cli_main(["--demo"]) == 0
    assert requested_modes[-1].value == "demo"


def test_no_subcommand_with_no_config_runs_setup(monkeypatch) -> None:
    called = {}

    def _fake_run_setup(_args):
        called["setup"] = True
        return 0

    monkeypatch.setattr("openminion.cli.commands.setup.run_setup", _fake_run_setup)
    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(should_launch_setup=True),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    rc = cli_main([])
    assert rc == 0
    assert called.get("setup") is True


def test_no_subcommand_with_should_fail_fast_exits_two(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(should_fail_fast=True),
    )
    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.format_fail_fast_message",
        lambda **kw: "remediation message",
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    with pytest.raises(SystemExit) as excinfo:
        cli_main([])
    assert excinfo.value.code == 2


def test_no_subcommand_pipe_with_data_dispatches_one_shot(monkeypatch, capsys) -> None:
    import io
    import sys as _sys

    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    # Non-TTY stdin with content.
    fake_stdin = io.StringIO("summarize the plan\n")
    fake_stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(_sys, "stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    called = {}

    def _fake_run(prompt_args):
        called["prompt"] = prompt_args.prompt
        return 0

    monkeypatch.setattr("openminion.cli.commands.run.run_openminion", _fake_run)

    rc = cli_main([])
    assert rc == 0
    assert called.get("prompt") == "summarize the plan"


def test_no_subcommand_pipe_with_no_data_prints_help(monkeypatch, capsys) -> None:
    import io
    import sys as _sys

    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    fake_stdin = io.StringIO("")
    fake_stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(_sys, "stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    called = {}

    def _fake_run_interactive(_args):
        called["interactive"] = True
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.interactive.run_interactive",
        _fake_run_interactive,
    )

    rc = cli_main([])
    assert rc == 1
    assert "interactive" not in called, (
        "empty stdin must not dispatch the interactive CLI; print_help applies"
    )


def test_no_subcommand_pipe_with_whitespace_only_data_prints_help(
    monkeypatch, capsys
) -> None:
    import io
    import sys as _sys

    monkeypatch.setattr(
        "openminion.services.bootstrap.onboarding.resolve_surface_onboarding_route",
        lambda **kw: _stub_route(),
    )
    fake_stdin = io.StringIO("   \n  \t\n")
    fake_stdin.isatty = lambda: False  # type: ignore[method-assign]
    monkeypatch.setattr(_sys, "stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    called = {}

    def _fake_run_interactive(_args):
        called["interactive"] = True
        return 0

    monkeypatch.setattr(
        "openminion.cli.commands.interactive.run_interactive",
        _fake_run_interactive,
    )

    rc = cli_main([])
    assert rc == 1
    assert "interactive" not in called


# ── Retired aliases stay retired ──────────────────────────────────────


def test_legacy_aliases_are_hidden_from_root_help(capsys) -> None:
    with pytest.raises(SystemExit):
        cli_main(["--help"])
    help_text = capsys.readouterr().out
    for alias in ("focus", "chat", "tui", "dashboard"):
        assert alias not in help_text


@pytest.mark.parametrize("alias", ("focus", "chat", "tui", "dashboard"))
def test_retired_aliases_are_rejected(alias: str) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_main([alias])
    assert excinfo.value.code == 2


def test_retired_product_command_modules_are_deleted() -> None:
    command_dir = Path(__file__).resolve().parents[2] / "src/openminion/cli/commands"
    assert not (command_dir / "chat.py").exists()
    assert not (command_dir / "tui.py").exists()
    assert not (command_dir / "aliases.py").exists()
