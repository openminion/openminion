from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from openminion.cli.tui.project_context import ProjectContextInfo
from openminion.cli.tui.terminal.shell import _push_greeter


class _StubRuntime:
    def __init__(
        self,
        *,
        agent_id: str = "test-agent",
        provider_name: str = "openai",
        model_name: str = "gpt-4",
        project_context: ProjectContextInfo | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.provider_name = provider_name
        self.model_name = model_name
        self.project_context = project_context


def _capture_greeter(*, working_dir: str = "/test/cwd") -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    _push_greeter(console, runtime=_StubRuntime(), working_dir=working_dir)
    return buf.getvalue()


def test_greeter_contains_openminion_identity() -> None:
    out = _capture_greeter()
    assert "openminion" in out


def test_greeter_contains_focus_shell_label() -> None:
    out = _capture_greeter()
    assert "focus shell" in out


def test_greeter_contains_agent_label_and_value() -> None:
    out = _capture_greeter()
    assert "agent:" in out
    assert "test-agent" in out


def test_greeter_contains_model_label_and_value() -> None:
    out = _capture_greeter()
    assert "model:" in out
    assert "openai/gpt-4" in out


def test_greeter_contains_cwd() -> None:
    out = _capture_greeter(working_dir="/my/special/dir")
    assert "cwd:" in out
    assert "/my/special/dir" in out


def test_greeter_contains_project_context_when_present() -> None:
    runtime = _StubRuntime(
        project_context=ProjectContextInfo(
            path=Path("/tmp/project/OPENMINION.md"),
            source_name="OPENMINION.md",
            size_bytes=123,
            content="rules",
        )
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    _push_greeter(console, runtime=runtime, working_dir="/tmp/project")
    out = buf.getvalue()
    assert "context:" in out
    assert "OPENMINION.md" in out
    assert "123 bytes" in out


def test_greeter_warns_for_legacy_context_name() -> None:
    runtime = _StubRuntime(
        project_context=ProjectContextInfo(
            path=Path("/tmp/project/AGENTS.md"),
            source_name="AGENTS.md",
            size_bytes=44,
            content="rules",
        )
    )
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    _push_greeter(console, runtime=runtime, working_dir="/tmp/project")
    out = buf.getvalue()
    assert "consider renaming to OPENMINION.md" in out


def test_greeter_renders_panel_border() -> None:
    out = _capture_greeter()
    has_box_char = any(
        c in out for c in ("╭", "╮", "╰", "╯", "│", "┌", "┐", "└", "┘", "─")
    )
    assert has_box_char, f"No box-drawing chars in greeter output: {out!r}"


def test_greeter_contains_inline_shortcut_hint() -> None:
    out = _capture_greeter()
    assert "/ for commands" in out
    assert "@ to mention a file" in out


def test_greeter_hint_does_not_contain_keybinding_reminders() -> None:
    out = _capture_greeter()
    assert "Enter to send" not in out
    assert "Shift+Enter" not in out
    assert "Esc to clear" not in out


def test_greeter_does_not_contain_try_block() -> None:
    out = _capture_greeter()
    assert "Try:" not in out


def test_greeter_does_not_contain_try_examples() -> None:
    out = _capture_greeter()
    assert "explain this codebase" not in out
    assert "find all references to" not in out
    assert "add tests for" not in out


def test_greeter_handles_missing_provider_gracefully() -> None:

    class _Bare:
        agent_id = "bare"
        model_name = "minimax-m2"

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    _push_greeter(console, runtime=_Bare(), working_dir="/tmp")
    out = buf.getvalue()
    assert "openminion" in out
    assert "bare" in out
    assert "minimax-m2" in out


def test_greeter_handles_bare_runtime() -> None:

    class _Bare:
        pass

    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    _push_greeter(console, runtime=_Bare(), working_dir="/tmp")
    out = buf.getvalue()
    assert "openminion" in out


def test_greeter_uses_rich_panel() -> None:
    import inspect

    from openminion.cli.tui.terminal import shell

    src = inspect.getsource(shell._push_greeter)
    assert "Panel" in src


def test_greeter_panel_uses_dim_border() -> None:
    import inspect

    from openminion.cli.tui.terminal import shell

    src = inspect.getsource(shell._push_greeter)
    assert 'border_style="dim"' in src
