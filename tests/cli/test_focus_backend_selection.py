from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.cli.commands.focus import _resolve_focus_backend


def _args(**overrides) -> SimpleNamespace:
    base = {"rich": False}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("env_value", "rich_flag", "expected"),
    [
        (None, False, "terminal"),
        (None, True, "textual"),
        ("textual", False, "textual"),
        ("terminal", False, "terminal"),
        ("garbage", False, "terminal"),
        ("textual", True, "textual"),
        ("terminal", True, "textual"),
    ],
)
def test_focus_backend_resolution(
    monkeypatch, env_value: str | None, rich_flag: bool, expected: str
) -> None:
    if env_value is None:
        monkeypatch.delenv("OPENMINION_FOCUS_BACKEND", raising=False)
    else:
        monkeypatch.setenv("OPENMINION_FOCUS_BACKEND", env_value)
    assert _resolve_focus_backend(_args(rich=rich_flag)) == expected


def test_focus_subparser_registers_rich_flag() -> None:
    import argparse

    from openminion.cli.commands import focus as focus_cmd

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    focus_cmd.register(subparsers)
    parsed = parser.parse_args(["focus", "--rich"])
    assert parsed.rich is True

    parsed_no_flag = parser.parse_args(["focus"])
    assert parsed_no_flag.rich is False


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
    )
    rc = focus_cmd.run_focus(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "requires an interactive terminal" in captured.err
    assert "drop the --rich flag" in captured.err or "terminal-flow" in captured.err


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
    )
    try:
        focus_cmd.run_focus(args)
    except Exception:
        pass
    assert silenced["called"] is True, (
        "with TTY available, --rich path must reach _silence_logging_for_tui; "
        "the non-TTY guard must NOT short-circuit"
    )
