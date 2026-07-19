from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.cli.commands.interactive import _resolve_interactive_backend


def _args(**overrides) -> SimpleNamespace:
    base = {"rich": False, "terminal": False}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("env_value", "terminal_flag", "rich_flag", "expected"),
    [
        (None, False, False, "terminal"),
        ("textual", False, False, "textual"),
        ("rich", False, False, "textual"),
        ("garbage", False, False, "terminal"),
        ("terminal", False, False, "terminal"),
        ("flow", False, False, "terminal"),
        ("terminal-flow", False, False, "terminal"),
        (None, True, False, "terminal"),
        (None, False, True, "textual"),
    ],
)
def test_interactive_backend_resolution(
    monkeypatch,
    env_value: str | None,
    terminal_flag: bool,
    rich_flag: bool,
    expected: str,
) -> None:
    if env_value is None:
        monkeypatch.delenv("OPENMINION_FOCUS_BACKEND", raising=False)
    else:
        monkeypatch.setenv("OPENMINION_FOCUS_BACKEND", env_value)
    assert (
        _resolve_interactive_backend(_args(terminal=terminal_flag, rich=rich_flag))
        == expected
    )


def test_focus_alias_registers_legacy_backend_flags() -> None:
    import argparse

    from openminion.cli.commands import interactive as interactive_cmd

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    interactive_cmd.register(subparsers)
    parsed = parser.parse_args(["focus", "--rich"])
    assert parsed.rich is True
    assert parsed.terminal is False

    parsed_terminal = parser.parse_args(["focus", "--terminal"])
    assert parsed_terminal.terminal is True
    assert parsed_terminal.rich is False

    parsed_no_flag = parser.parse_args(["focus"])
    assert parsed_no_flag.rich is False
    assert parsed_no_flag.terminal is False
    assert parsed_no_flag.animation_provider is None
    assert parsed_no_flag.animation is None

    parsed_animation = parser.parse_args(
        ["focus", "--animation-provider", "unicode", "--animation", "helix"]
    )
    assert parsed_animation.animation_provider == "unicode"
    assert parsed_animation.animation == "helix"


def test_default_backend_launches_terminal_flow_without_textual_tty_gate(
    monkeypatch,
) -> None:
    from openminion.cli.commands import interactive as interactive_cmd

    monkeypatch.delenv("OPENMINION_FOCUS_BACKEND", raising=False)
    monkeypatch.setattr(
        interactive_cmd,
        "_inspect_interactive_onboarding",
        lambda args: SimpleNamespace(action=None),
    )
    monkeypatch.setattr(
        interactive_cmd, "_silence_logging_for_interactive", lambda _args: ""
    )
    monkeypatch.setattr(
        interactive_cmd,
        "_enforce_textual_tty_requirement",
        lambda: pytest.fail("terminal-flow must not use the Textual TTY gate"),
    )
    launched: list[str] = []
    monkeypatch.setattr(
        interactive_cmd,
        "_launch_terminal_focus",
        lambda _args, _runtime, *, working_dir: launched.append(working_dir) or 0,
    )
    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        classmethod(lambda cls, *a, **kw: SimpleNamespace(close=lambda: None)),
    )
    monkeypatch.setattr(
        "openminion.cli.status.surface.record_surface_event",
        lambda *args, **kwargs: None,
    )

    args = SimpleNamespace(
        rich=False,
        config=None,
        home_root=None,
        data_root=None,
        agent=None,
        session=None,
        dir=".",
        no_interactive=False,
        no_context=False,
        no_update_check=True,
        theme=None,
        terminal=False,
    )
    assert interactive_cmd.run_interactive(args) == 0
    assert len(launched) == 1


def test_terminal_focus_starts_fresh_unless_session_is_requested(monkeypatch) -> None:
    from openminion.cli.commands import interactive as interactive_cmd

    created: list[str] = []
    constructor_calls: list[dict[str, object]] = []

    class _Runtime:
        def __init__(self, _runtime, **kwargs) -> None:
            constructor_calls.append(dict(kwargs))

        def create_new_session(self) -> str:
            created.append("focus-new")
            return "focus-new"

        def set_project_context(self, _context) -> None:
            return None

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.OpenMinionRuntime", _Runtime
    )
    monkeypatch.setattr(
        "openminion.cli.interactive.terminal.run_terminal_focus",
        lambda *_args, **_kwargs: 0,
    )

    base_args = dict(
        agent="minimax-m2-7",
        no_context=True,
        plain_spinner=False,
        verbosity="normal",
        no_update_check=True,
    )
    interactive_cmd._launch_terminal_focus(
        SimpleNamespace(session=None, **base_args),
        object(),
        working_dir="/tmp/project",
    )
    interactive_cmd._launch_terminal_focus(
        SimpleNamespace(session="focus-existing", **base_args),
        object(),
        working_dir="/tmp/project",
    )

    assert created == ["focus-new"]
    assert constructor_calls == [
        {
            "target": "focus",
            "agent_id": "minimax-m2-7",
            "working_dir": "/tmp/project",
            "bind_immediately": False,
            "session_id": None,
        },
        {
            "target": "focus",
            "agent_id": "minimax-m2-7",
            "working_dir": "/tmp/project",
            "bind_immediately": False,
            "session_id": "focus-existing",
        },
    ]


def test_rich_without_tty_emits_helpful_error(monkeypatch, capsys) -> None:
    from openminion.cli.commands import interactive as interactive_cmd

    monkeypatch.setattr(
        interactive_cmd,
        "_inspect_interactive_onboarding",
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
    rc = interactive_cmd.run_interactive(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "requires an interactive terminal" in captured.err
    assert "pipe a prompt" in captured.err


def test_rich_with_tty_does_not_short_circuit(monkeypatch) -> None:
    from openminion.cli.commands import interactive as interactive_cmd

    monkeypatch.setattr(
        interactive_cmd,
        "_inspect_interactive_onboarding",
        lambda args: SimpleNamespace(action=None),
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    silenced = {"called": False}

    def _silence(args):
        silenced["called"] = True
        return None

    monkeypatch.setattr(interactive_cmd, "_silence_logging_for_interactive", _silence)
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
        interactive_cmd.run_interactive(args)
    except Exception:
        pass
    assert silenced["called"] is True, (
        "with TTY available, --rich path must reach interactive logging setup; "
        "the non-TTY guard must NOT short-circuit"
    )
