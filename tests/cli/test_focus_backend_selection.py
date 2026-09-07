from __future__ import annotations

from types import SimpleNamespace

from openminion.cli.presentation.animation import AnimationResolution, AnimationSpec


def test_interactive_launches_terminal_flow(monkeypatch) -> None:
    from openminion.cli.commands import interactive as interactive_cmd
    from openminion.cli.presentation import styles

    monkeypatch.setattr(
        interactive_cmd,
        "_inspect_interactive_onboarding",
        lambda _args: SimpleNamespace(action=None),
    )
    monkeypatch.setattr(
        interactive_cmd, "_silence_logging_for_interactive", lambda _args: ""
    )
    launched: list[str] = []
    monkeypatch.setattr(
        interactive_cmd,
        "_launch_terminal_focus",
        lambda _args, _runtime, *, working_dir, **_kwargs: (
            launched.append(working_dir) or 0
        ),
    )
    monkeypatch.setattr(
        "openminion.api.runtime.APIRuntime.from_config_path",
        classmethod(lambda cls, *args, **kwargs: SimpleNamespace(close=lambda: None)),
    )
    monkeypatch.setattr(
        "openminion.cli.status.surface.record_surface_event",
        lambda *args, **kwargs: None,
    )

    args = SimpleNamespace(
        config=None,
        home_root=None,
        data_root=None,
        agent=None,
        session=None,
        dir=".",
        add_dir=[],
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
