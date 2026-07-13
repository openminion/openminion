from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.cli.commands.focus import _legacy_terminal_requested


def _args(**overrides) -> SimpleNamespace:
    base = {"rich": False, "terminal": False}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("env_value", "terminal_flag", "expected"),
    [
        (None, False, False),
        ("textual", False, False),
        ("garbage", False, False),
        ("terminal", False, True),
        ("flow", False, True),
        ("terminal-flow", False, True),
        (None, True, True),
    ],
)
def test_legacy_terminal_request_detection(
    monkeypatch, env_value: str | None, terminal_flag: bool, expected: bool
) -> None:
    if env_value is None:
        monkeypatch.delenv("OPENMINION_FOCUS_BACKEND", raising=False)
    else:
        monkeypatch.setenv("OPENMINION_FOCUS_BACKEND", env_value)
    assert _legacy_terminal_requested(_args(terminal=terminal_flag)) is expected


def test_focus_subparser_registers_rich_and_terminal_flags() -> None:
    import argparse

    from openminion.cli.commands import focus as focus_cmd

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    focus_cmd.register(subparsers)
    parsed = parser.parse_args(["focus", "--rich"])
    assert parsed.rich is True
    assert parsed.terminal is False

    parsed_terminal = parser.parse_args(["focus", "--terminal"])
    assert parsed_terminal.terminal is True
    assert parsed_terminal.rich is False

    parsed_no_flag = parser.parse_args(["focus"])
    assert parsed_no_flag.rich is False
    assert parsed_no_flag.terminal is False


def test_legacy_terminal_flag_returns_migration_error(monkeypatch, capsys) -> None:
    from openminion.cli.commands import focus as focus_cmd

    monkeypatch.delenv("OPENMINION_FOCUS_BACKEND", raising=False)
    assert focus_cmd.run_focus(_args(terminal=True)) == 2
    assert "legacy terminal-flow renderer has been retired" in capsys.readouterr().err


def test_rich_without_tty_emits_helpful_error(monkeypatch, capsys) -> None:
    from openminion.cli.commands import focus as focus_cmd

    monkeypatch.setattr(
        focus_cmd,
        "_inspect_tui_onboarding",
        lambda args: SimpleNamespace(action=None),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    args = SimpleNamespace(
        rich=True,
        config=None,
        home_root=None,
        data_root=None,
        agent=None,
        session=None,
        dir=None,
        no_interactive=False,
        theme=None,
        terminal=False,
    )
    rc = focus_cmd.run_focus(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "requires an interactive terminal" in captured.err
    assert "pipe a prompt" in captured.err


def test_rich_with_tty_does_not_short_circuit(monkeypatch) -> None:
    from openminion.cli.commands import focus as focus_cmd

    monkeypatch.setattr(
        focus_cmd,
        "_inspect_tui_onboarding",
        lambda args: SimpleNamespace(action=None),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    silenced = {"called": False}

    def _silence(args):
        silenced["called"] = True
        return None

    monkeypatch.setattr(focus_cmd, "_silence_logging_for_tui", _silence)
    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        classmethod(lambda cls, *a, **kw: SimpleNamespace(close=lambda: None)),
        raising=False,
    )

    args = SimpleNamespace(
        rich=True,
        config=None,
        home_root=None,
        data_root=None,
        agent=None,
        session=None,
        dir=".",
        no_interactive=False,
        theme=None,
        terminal=False,
    )
    try:
        focus_cmd.run_focus(args)
    except Exception:
        pass
    assert silenced["called"] is True, (
        "with TTY available, --rich path must reach _silence_logging_for_tui; "
        "the non-TTY guard must NOT short-circuit"
    )
