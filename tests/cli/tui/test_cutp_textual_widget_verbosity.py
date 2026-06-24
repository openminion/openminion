from __future__ import annotations

import argparse
from textual.css.query import QueryError

from openminion.cli.tui.presentation.models import ToolEvent
from openminion.cli.tui.presentation.tool.blocks import (
    ToolBlockWidget,
    _NORMAL_LINE_CAP,
    _VERBOSE_LINE_CAP,
)


def test_tool_block_defaults_to_normal_verbosity() -> None:
    event = ToolEvent(
        tool_name="exec.run", args={"command": "ls"}, content="x", exit_code=0
    )
    widget = ToolBlockWidget(event, pending=False)
    assert widget.verbosity == "normal"


def test_tool_block_honors_constructor_verbosity_kwarg() -> None:
    event = ToolEvent(
        tool_name="exec.run", args={"command": "ls"}, content="x", exit_code=0
    )
    widget = ToolBlockWidget(event, pending=False, verbosity="quiet")
    assert widget.verbosity == "quiet"
    widget = ToolBlockWidget(event, pending=False, verbosity="verbose")
    assert widget.verbosity == "verbose"


def test_tool_block_verbosity_invalid_kwarg_falls_back_to_normal() -> None:
    event = ToolEvent(
        tool_name="exec.run", args={"command": "ls"}, content="x", exit_code=0
    )
    widget = ToolBlockWidget(event, pending=False, verbosity="bogus")  # type: ignore[arg-type]
    assert widget.verbosity == "normal"


def test_tool_block_line_caps_match_ladder() -> None:
    event = ToolEvent(
        tool_name="exec.run", args={"command": "ls"}, content="x", exit_code=0
    )
    widget = ToolBlockWidget(event, pending=False, verbosity="normal")
    assert widget._verbosity_line_cap() == _NORMAL_LINE_CAP
    widget.verbosity = "verbose"  # type: ignore[assignment]
    assert widget._verbosity_line_cap() == _VERBOSE_LINE_CAP
    widget.verbosity = "quiet"  # type: ignore[assignment]
    assert widget._verbosity_line_cap() == 0


def test_tool_block_verbose_render_uses_200_line_cap() -> None:
    content = "\n".join(f"line-{i}" for i in range(150))
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "long"},
        content=content,
        exit_code=0,
    )
    widget = ToolBlockWidget(event, pending=False, verbosity="verbose")
    rendered = widget._render_exec()
    plain = rendered.plain
    assert "show more" not in plain
    assert "line-149" in plain


def test_tool_block_normal_render_uses_6_line_cap() -> None:
    content = "\n".join(f"line-{i}" for i in range(20))
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "long"},
        content=content,
        exit_code=0,
    )
    widget = ToolBlockWidget(event, pending=False, verbosity="normal")
    rendered = widget._render_exec()
    plain = rendered.plain
    assert "show more" in plain
    assert "line-5" in plain  # last shown line (0-indexed)
    assert "line-6" not in plain


def test_focus_transcript_default_verbosity_is_normal() -> None:
    from openminion.cli.tui.focus.widgets.transcript import FocusTranscript

    transcript = FocusTranscript()
    assert transcript.verbosity == "normal"


def test_focus_transcript_accepts_verbosity_kwarg() -> None:
    from openminion.cli.tui.focus.widgets.transcript import FocusTranscript

    transcript = FocusTranscript(verbosity="quiet")
    assert transcript.verbosity == "quiet"
    transcript = FocusTranscript(verbosity="verbose")
    assert transcript.verbosity == "verbose"


def test_focus_transcript_set_verbosity_updates_property() -> None:
    from openminion.cli.tui.focus.widgets.transcript import FocusTranscript

    transcript = FocusTranscript()
    transcript.set_verbosity("quiet")
    assert transcript.verbosity == "quiet"
    transcript.set_verbosity("verbose")
    assert transcript.verbosity == "verbose"
    transcript.set_verbosity("bogus")  # type: ignore[arg-type]
    assert transcript.verbosity == "verbose"


def test_focus_screen_slash_registry_contains_verbosity_slashes() -> None:
    from openminion.cli.tui.focus.screen import FocusScreen

    descriptor = FocusScreen._slash_command_registry  # type: ignore[attr-defined]

    class _Stub:
        pass

    stub = _Stub()
    rows = descriptor.fget(stub)  # type: ignore[union-attr]
    aliases = {alias for aliases, _desc, _handler in rows for alias in aliases}
    assert "/quiet" in aliases
    assert "/normal" in aliases
    assert "/verbose" in aliases

    handlers = {handler for _aliases, _desc, handler in rows}
    assert "_slash_quiet" in handlers
    assert "_slash_normal" in handlers
    assert "_slash_verbose" in handlers


def test_focus_screen_has_verbosity_handlers() -> None:
    from openminion.cli.tui.focus.screen import FocusScreen

    assert callable(getattr(FocusScreen, "_slash_quiet", None))
    assert callable(getattr(FocusScreen, "_slash_normal", None))
    assert callable(getattr(FocusScreen, "_slash_verbose", None))
    assert callable(getattr(FocusScreen, "_apply_session_verbosity", None))


def test_focus_screen_apply_session_verbosity_ignores_missing_transcript() -> None:
    from openminion.cli.tui.focus.screen import FocusScreen

    class _Stub:
        _verbosity = "normal"

        def query_one(self, *_args, **_kwargs):
            raise QueryError("missing transcript")

    stub = _Stub()

    FocusScreen._apply_session_verbosity(stub, "quiet")

    assert stub._verbosity == "quiet"


def test_focus_screen_cycle_permission_mode_ignores_missing_transcript() -> None:
    from openminion.cli.tui.focus.screen import FocusScreen

    class _Stub:
        def _cycle_permission_mode_from_ui(self) -> str:
            return "readonly"

        def query_one(self, *_args, **_kwargs):
            raise QueryError("missing transcript")

    FocusScreen.action_cycle_permission_mode(_Stub())


def test_focus_app_accepts_verbosity_kwarg() -> None:
    from openminion.cli.tui.focus.app import FocusApp

    app = FocusApp(verbosity="quiet")
    assert app._verbosity == "quiet"
    app = FocusApp(verbosity="verbose")
    assert app._verbosity == "verbose"
    app = FocusApp()
    assert app._verbosity == "normal"


def test_focus_app_verbosity_invalid_falls_back_to_normal() -> None:
    from openminion.cli.tui.focus.app import FocusApp

    app = FocusApp(verbosity="bogus")
    assert app._verbosity == "normal"


def test_focus_register_adds_verbosity_flag() -> None:
    from openminion.cli.commands.focus import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    args = parser.parse_args(["focus", "--verbosity", "quiet"])
    assert getattr(args, "verbosity", None) == "quiet"
    args = parser.parse_args(["focus", "--verbosity", "verbose"])
    assert getattr(args, "verbosity", None) == "verbose"


def test_focus_register_verbosity_default_is_none() -> None:
    from openminion.cli.commands.focus import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    register(subparsers)
    args = parser.parse_args(["focus"])
    assert getattr(args, "verbosity", "MISSING") is None
