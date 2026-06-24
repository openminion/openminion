from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


# ── Identity: shared owners expose one set of objects ─────────────────────────


def test_chat_models_come_from_shared_owner() -> None:
    from openminion.cli.tui.presentation.models import (
        ChatMessage,
        MessageKind,
        ToolEvent,
    )
    from openminion.cli.tui.widgets import (
        ChatMessage as WChatMessage,
    )
    from openminion.cli.tui.widgets import (
        MessageKind as WMessageKind,
    )
    from openminion.cli.tui.widgets import (
        ToolEvent as WToolEvent,
    )
    from openminion.cli.tui.widgets.chat import (
        ChatMessage as WCChatMessage,
    )
    from openminion.cli.tui.widgets.chat import (
        MessageKind as WCMessageKind,
    )
    from openminion.cli.tui.widgets.chat import (
        ToolEvent as WCToolEvent,
    )

    assert ChatMessage is WChatMessage is WCChatMessage
    assert MessageKind is WMessageKind is WCMessageKind
    assert ToolEvent is WToolEvent is WCToolEvent


def test_tool_block_widget_is_shared() -> None:
    from openminion.cli.tui.focus.widgets.tool_block import (
        ToolBlockWidget as FocusToolBlock,
    )
    from openminion.cli.tui.presentation.tool.blocks import ToolBlockWidget
    from openminion.cli.tui.widgets.chat import ToolBlockWidget as DashboardToolBlock

    assert ToolBlockWidget is FocusToolBlock
    assert ToolBlockWidget is DashboardToolBlock


def test_thinking_indicator_is_shared() -> None:
    from openminion.cli.tui.presentation.status import (
        ThinkingIndicator as SharedThinkingIndicator,
    )
    from openminion.cli.tui.tabs.chat import (
        ThinkingIndicator as DashboardThinkingIndicator,
    )

    assert SharedThinkingIndicator is DashboardThinkingIndicator


def test_clipboard_is_shared() -> None:
    from openminion.cli.tui.presentation.clipboard import copy_to_clipboard
    from openminion.cli.tui.tabs.chat import copy_to_clipboard as chat_copy

    assert chat_copy is copy_to_clipboard


def test_tool_context_hint_is_shared() -> None:
    from openminion.cli.tui.focus.widgets.tool_block import (
        tool_context_hint as focus_hint,
    )
    from openminion.cli.tui.presentation.tool.blocks import tool_context_hint

    assert tool_context_hint is focus_hint
    assert tool_context_hint("exec.run", {"command": "ls"}) == "ls"
    assert tool_context_hint("file.read", {"path": "foo.py"}) == "foo.py"
    assert tool_context_hint("fetch.get", {"url": "https://x"}) == "https://x"
    assert tool_context_hint("something.else", {}) == ""


def test_progress_label_is_shared() -> None:
    from openminion.cli.tui.presentation.status import format_progress_label

    # Fallback kicks in when the payload cannot produce a label.
    assert format_progress_label({}, fallback_label="Working...") == "Working..."


# ── Ownership direction: shared owners do not reach into shells ──────────────

_SHARED_OWNER_MODULES = [
    "openminion/src/openminion/cli/tui/presentation/models.py",
    "openminion/src/openminion/cli/tui/presentation/status.py",
    "openminion/src/openminion/cli/tui/presentation/clipboard.py",
    "openminion/src/openminion/cli/tui/presentation/tool/blocks.py",
    "openminion/src/openminion/cli/tui/presentation/tool/progress.py",
    "openminion/src/openminion/cli/tui/presentation/header.py",
    "openminion/src/openminion/cli/tui/widgets/chat.py",
]

_FORBIDDEN_PREFIXES = (
    "openminion.cli.tui.focus.",
    "openminion.cli.tui.tabs.",
)


def _collect_import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


@pytest.fixture(scope="module")
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("rel_path", _SHARED_OWNER_MODULES)
def test_shared_owners_do_not_import_shell_modules(
    _repo_root: Path, rel_path: str
) -> None:
    path = _repo_root / rel_path
    imports = _collect_import_names(path)
    bad = [name for name in imports if name.startswith(_FORBIDDEN_PREFIXES)]
    assert not bad, (
        f"{rel_path} imports shell-local module(s): {bad}. "
        "Shared owners must not reach into focus/ or tabs/."
    )


def test_focus_screen_does_not_import_dashboard_chat(_repo_root: Path) -> None:
    path = _repo_root / "openminion/src/openminion/cli/tui/focus/screen.py"
    imports = _collect_import_names(path)
    assert "openminion.cli.tui.tabs.chat" not in imports, (
        "focus/screen.py must not import from openminion.cli.tui.tabs.chat; "
        "use openminion.cli.tui.presentation instead."
    )


# ── Header facts ──────────────────────────────────────────────────────────────


def test_header_helpers_format_shared_facts() -> None:
    import os

    from openminion.cli.tui.presentation.header import (
        RuntimeHeaderContext,
        format_clock,
        shorten_session_id,
        shorten_working_dir,
    )

    # Session shortening is deterministic.
    assert shorten_session_id("sess-abc-1234567890", length=8) == "sess-abc"

    # Working-dir shortening normalizes home-prefixed paths.
    home = os.path.expanduser("~")
    assert shorten_working_dir(home) == "~"
    assert shorten_working_dir(os.path.join(home, "repos")) == "~/repos"

    # Clock format is HH:MM.
    clock = format_clock()
    assert len(clock) == 5 and clock[2] == ":"

    long_dir = "/very/deep/nested/path/that/should/be/shortened/for/display/purposes"
    short = shorten_working_dir(long_dir, max_length=40)
    assert len(short) <= 40, f"shortened path too long: {short!r} ({len(short)} chars)"
    assert short.startswith("/") and "…" in short, (
        f"expected mid-elided form like `/x/…/end`, got {short!r}"
    )

    # Short paths pass through unchanged.
    assert shorten_working_dir("/usr/bin", max_length=40) == "/usr/bin"

    # Shared model surfaces the same labels to both shells.
    ctx = RuntimeHeaderContext(
        agent_id="alice", session_id="sess-abc-1234567890", working_dir=home
    )
    labels = ctx.segment_labels()
    assert labels["agent_id"] == "alice"
    assert labels["session_id"] == "sess-abc"
    assert labels["working_dir"] == "~"


# ── Tool-progress mapper ─────────────────────────────────────────────────────


def test_build_tool_event_from_progress_normalizes_payload() -> None:
    from openminion.cli.tui.presentation.tool.progress import (
        build_tool_event_from_progress,
    )

    event = build_tool_event_from_progress(
        {
            "tool_name": "exec.run",
            "args": {"command": "ls"},
            "content": "file1\nfile2\n",
            "duration_ms": "42",
            "exit_code": 0,
            "truncated": True,
        }
    )
    assert event.tool_name == "exec.run"
    assert event.args == {"command": "ls"}
    assert event.content == "file1\nfile2\n"
    assert event.full_content == event.content
    assert event.duration_ms == 42
    assert event.exit_code == 0
    assert event.truncated is True


def test_build_tool_event_applies_normalize_args() -> None:
    from openminion.cli.tui.presentation.tool.progress import (
        build_tool_event_from_progress,
    )

    def _relativize(args: dict) -> dict:
        out = dict(args)
        if "path" in out:
            out["path"] = out["path"].rsplit("/", 1)[-1]
        return out

    event = build_tool_event_from_progress(
        {
            "tool_name": "file.read",
            "args": {"path": "/abs/path/to/foo.py"},
            "content": "hello",
        },
        normalize_args=_relativize,
    )
    assert event.args == {"path": "foo.py"}


# ── Owner-attachment sanity check ────────────────────────────────────────────


def test_presentation_package_reexports() -> None:
    mod = importlib.import_module("openminion.cli.tui.presentation")
    expected = {
        "ChatMessage",
        "MessageKind",
        "ToolEvent",
        "ThinkingIndicator",
        "ToolBlockWidget",
        "RuntimeHeaderContext",
        "build_tool_event_from_progress",
        "copy_to_clipboard",
        "format_chat_timestamp",
        "format_clock",
        "format_progress_label",
        "shorten_session_id",
        "shorten_working_dir",
        "tool_call_body",
        "tool_context_hint",
    }
    missing = expected - set(dir(mod))
    assert not missing, f"presentation package missing exports: {missing}"


# ── Dashboard adopts shared ToolBlockWidget for tool_event messages ──────────


def test_dashboard_push_tool_event_mounts_shared_tool_block() -> None:
    import inspect

    from openminion.cli.tui.presentation.tool.blocks import ToolBlockWidget
    from openminion.cli.tui.widgets.chat import MessageWidget

    compose_src = inspect.getsource(MessageWidget.compose)
    assert "ToolBlockWidget" in compose_src, (
        "MessageWidget.compose no longer yields ToolBlockWidget; the shared "
        "conversation owner must mount the shared widget for tool_event messages."
    )
    assert "msg.tool_event" in compose_src, (
        "MessageWidget.compose must branch on `msg.tool_event` to mount the "
        "shared ToolBlockWidget."
    )
    # Identity check: the class MessageWidget.compose references is the same
    # shared owner both shells consume.
    mod = importlib.import_module("openminion.cli.tui.widgets.chat")
    assert getattr(mod, "ToolBlockWidget", None) is ToolBlockWidget


# ── Focus feature verification on shared owners (TUISPF-05/06/07) ─────────────


def test_focus_live_and_history_both_flow_through_shared_tool_block() -> None:
    import inspect

    from openminion.cli.tui.focus.screen import FocusScreen

    focus_src = inspect.getsource(FocusScreen)
    # Live progress path
    assert "build_tool_event_from_progress" in focus_src
    assert "tool_call_body" in focus_src
    # History replay path: runtime message normalization populates tool_event
    # via the shared RuntimeMessageMixin helper.
    from openminion.cli.tui.providers.runtime.messages import RuntimeMessageMixin

    runtime_src = inspect.getsource(RuntimeMessageMixin)
    assert "_tool_event_from_metadata" in runtime_src
    # Both paths produce the same shared ToolEvent type.
    from openminion.cli.tui.presentation.models import ToolEvent as SharedToolEvent
    from openminion.cli.tui.presentation.tool.progress import (
        build_tool_event_from_progress,
    )

    built = build_tool_event_from_progress(
        {"tool_name": "file.read", "args": {"path": "x.py"}, "content": "hi"}
    )
    assert isinstance(built, SharedToolEvent)


def test_focus_directory_session_affinity_uses_shared_runtime() -> None:
    import inspect

    from openminion.cli.tui.focus.screen import FocusScreen
    from openminion.cli.tui.providers.runtime import OpenMinionRuntime

    focus_src = inspect.getsource(FocusScreen)
    assert "find_candidate_session" in focus_src
    assert "bind_session" in focus_src
    assert "create_new_session" in focus_src

    runtime_src = inspect.getsource(OpenMinionRuntime)
    # All three methods live on the shared runtime adapter.
    for name in (
        "def find_candidate_session",
        "def bind_session",
        "def create_new_session",
        "def list_directory_sessions",
    ):
        assert name in runtime_src, f"shared runtime missing {name!r}"
    # And the new-session path tags session metadata with working_dir.
    assert "update_session_metadata" in runtime_src


def test_focus_inline_approval_uses_shared_approval_callback() -> None:
    import inspect

    from openminion.cli.tui.focus.screen import FocusScreen
    from openminion.cli.tui.providers.runtime import OpenMinionRuntime

    focus_src = inspect.getsource(FocusScreen)
    # FocusScreen passes approval_callback into send_message().
    assert "approval_callback=self._approval_callback" in focus_src
    assert "ToolApprovalWidget" in focus_src
    # Shared runtime forwards approval_callback through send_message.
    send_src = inspect.getsource(OpenMinionRuntime.send_message)
    assert "approval_callback" in send_src


def test_dashboard_progress_callback_builds_shared_tool_event() -> None:
    import inspect

    from openminion.cli.tui.tabs.chat.turns import ChatTurnMixin

    # The dashboard turn mixin owns tool_started/tool_completed normalization.
    src = inspect.getsource(ChatTurnMixin)
    assert "build_tool_event_from_progress" in src, (
        "Dashboard ChatTab no longer uses the shared tool-event builder; "
        "it must route tool_started/tool_completed payloads through "
        "`openminion.cli.tui.presentation.tool.progress.build_tool_event_from_progress`."
    )
    assert "tool_call_body" in src, (
        "Dashboard ChatTab no longer uses the shared `tool_call_body`; "
        "tool messages must reuse the shared body formatter."
    )
