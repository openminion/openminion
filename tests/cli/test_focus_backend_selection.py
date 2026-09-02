from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from openminion.cli.commands.interactive import _resolve_interactive_backend
from openminion.cli.presentation.animation import AnimationResolution, AnimationSpec


def _args(**overrides) -> SimpleNamespace:
    base = {"rich": False}
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    ("rich_flag", "expected"),
    [
        (False, "terminal"),
        (True, "textual"),
    ],
)
def test_interactive_backend_resolution(
    rich_flag: bool,
    expected: str,
) -> None:
    assert _resolve_interactive_backend(_args(rich=rich_flag)) == expected


def test_default_backend_launches_terminal_flow_without_textual_tty_gate(
    monkeypatch,
) -> None:
    from openminion.cli.commands import interactive as interactive_cmd
    from openminion.cli.presentation import styles

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
        color="always",
    )
    try:
        assert interactive_cmd.run_interactive(args) == 0
        assert styles.get_color_mode() == "on"
        assert len(launched) == 1
    finally:
        styles.set_color_mode(None)


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


def test_terminal_focus_receives_selected_activity_animation(monkeypatch) -> None:
    from openminion.cli.commands import interactive as interactive_cmd

    animation = AnimationSpec("unicode", "helix", ("◐", "◓"), 100)
    resolution = AnimationResolution(animation, source="cli")
    launch_kwargs: list[dict[str, object]] = []

    class _Runtime:
        def __init__(self, _runtime, **_kwargs) -> None:
            return None

        def create_new_session(self) -> str:
            return "focus-new"

    monkeypatch.setattr(
        "openminion.cli.interactive.runtime.OpenMinionRuntime", _Runtime
    )
    monkeypatch.setattr(
        "openminion.cli.presentation.animation.resolve_focus_animation",
        lambda _args: resolution,
    )
    monkeypatch.setattr(
        "openminion.cli.interactive.terminal.run_terminal_focus",
        lambda *_args, **kwargs: launch_kwargs.append(kwargs) or 0,
    )

    interactive_cmd._launch_terminal_focus(
        SimpleNamespace(
            agent=None,
            session=None,
            no_context=True,
            plain_spinner=False,
            progress="full",
            verbosity="normal",
            no_update_check=True,
        ),
        object(),
        working_dir="/tmp/project",
    )

    assert launch_kwargs[0]["animation"] is resolution
    assert launch_kwargs[0]["progress"] == "full"


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
    )
    rc = interactive_cmd.run_interactive(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "requires an interactive terminal" in captured.err
    assert "pipe a prompt" in captured.err


def test_rich_missing_textual_reports_exact_extra(monkeypatch, capsys) -> None:
    from openminion.cli.commands import interactive as interactive_cmd

    monkeypatch.setattr(
        interactive_cmd,
        "_inspect_interactive_onboarding",
        lambda args: SimpleNamespace(action=None),
    )
    monkeypatch.setattr(
        interactive_cmd, "_silence_logging_for_interactive", lambda _args: ""
    )
    monkeypatch.setattr(
        interactive_cmd, "_enforce_textual_tty_requirement", lambda: None
    )
    real_import_module = importlib.import_module

    def fail_textual(name: str, package: str | None = None):
        if name == "openminion.cli.interactive.app":
            raise ModuleNotFoundError("No module named 'textual'", name="textual")
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fail_textual)

    assert interactive_cmd.run_interactive(_args(rich=True)) == 2
    assert capsys.readouterr().err.strip() == (
        "openminion --rich requires the Textual renderer. "
        "Install it with: pip install 'openminion[textual]'"
    )


@pytest.mark.parametrize("missing_name", ["unexpected_dependency", "textual.internal"])
def test_rich_unrelated_import_failure_keeps_startup_error_owner(
    monkeypatch, capsys, missing_name: str
) -> None:
    from openminion.cli.commands import interactive as interactive_cmd

    monkeypatch.setattr(
        interactive_cmd,
        "_inspect_interactive_onboarding",
        lambda args: SimpleNamespace(action=None),
    )
    monkeypatch.setattr(
        interactive_cmd, "_silence_logging_for_interactive", lambda _args: ""
    )
    monkeypatch.setattr(
        interactive_cmd, "_enforce_textual_tty_requirement", lambda: None
    )
    real_import_module = importlib.import_module

    def fail_backend(name: str, package: str | None = None):
        if name == "openminion.cli.interactive.app":
            raise ModuleNotFoundError(
                f"No module named {missing_name!r}",
                name=missing_name,
            )
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fail_backend)

    assert interactive_cmd.run_interactive(_args(rich=True)) == 1
    assert "openminion: interactive startup error" in capsys.readouterr().err


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
    )
    try:
        interactive_cmd.run_interactive(args)
    except Exception:
        pass
    assert silenced["called"] is True, (
        "with TTY available, --rich path must reach interactive logging setup; "
        "the non-TTY guard must NOT short-circuit"
    )
