from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from openminion.cli.tui.presentation.models import ToolEvent
from openminion.cli.tui.presentation.tool.blocks import (
    ToolBlockWidget,
    _TOOL_VERBS,
    verbs_for_tool,
)


@pytest.mark.parametrize(
    "tool_name,present,past",
    [
        ("exec.run", "Running", "Ran"),
        ("file.read", "Reading", "Read"),
        ("file.edit", "Editing", "Edited"),
        ("file.write", "Writing", "Wrote"),
    ],
)
def test_canonical_verbs_for_known_tools(
    tool_name: str, present: str, past: str
) -> None:
    assert verbs_for_tool(tool_name) == (present, past)


@pytest.mark.parametrize("tool_name", ["fetch.url", "fetch.html", "fetch.api"])
def test_fetch_prefix_resolves_to_fetching_fetched(tool_name: str) -> None:
    assert verbs_for_tool(tool_name) == ("Fetching", "Fetched")


@pytest.mark.parametrize("tool_name", ["search.brave", "search.web", "search.google"])
def test_search_prefix_resolves_to_searching_searched(tool_name: str) -> None:
    assert verbs_for_tool(tool_name) == ("Searching", "Searched")


def test_unknown_tool_falls_back_to_running_ran() -> None:
    assert verbs_for_tool("custom.tool") == ("Running", "Ran")
    assert verbs_for_tool("") == ("Running", "Ran")


def test_pending_exec_run_uses_running() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "pytest -x"},
        content="",
    )
    title = ToolBlockWidget(event, pending=True)._header_text()
    assert "Running" in title
    assert "pytest -x" in title
    assert "·" not in title  # no duration separator while pending


def test_completed_exec_run_uses_ran_with_duration() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "ls"},
        content="file1\n",
        duration_ms=1234,
        exit_code=0,
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "Ran" in title
    assert "ls" in title
    assert "1s" in title
    assert "·" in title


def test_completed_exec_run_short_duration_uses_ms() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "echo hi"},
        content="hi",
        duration_ms=300,
        exit_code=0,
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "<1s" in title


def test_pending_file_edit_uses_editing() -> None:
    event = ToolEvent(
        tool_name="file.edit",
        args={"path": "src/foo.py"},
        content="",
    )
    title = ToolBlockWidget(event, pending=True)._header_text()
    assert "Editing" in title
    assert "src/foo.py" in title


def test_completed_file_edit_uses_edited() -> None:
    event = ToolEvent(
        tool_name="file.edit",
        args={"path": "src/foo.py"},
        content="@@ -1 +1 @@\n-a\n+b",
        duration_ms=800,
        exit_code=0,
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "Edited" in title
    assert "src/foo.py" in title


def test_pending_fetch_uses_fetching() -> None:
    event = ToolEvent(
        tool_name="fetch.url",
        args={"url": "https://example.com"},
        content="",
    )
    title = ToolBlockWidget(event, pending=True)._header_text()
    assert "Fetching" in title
    assert "https://example.com" in title


def test_completed_search_uses_searched() -> None:
    event = ToolEvent(
        tool_name="search.brave",
        args={"query": "openminion"},
        content="...",
        duration_ms=2100,
        exit_code=0,
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "Searched" in title


def test_failed_tool_shows_exit_code_when_no_duration() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "false"},
        content="",
        exit_code=1,
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "Ran" in title
    assert "exit 1" in title


def test_verb_table_is_only_place_verbs_are_spelled() -> None:
    src_root = Path(__file__).resolve().parents[4] / "src" / "openminion"
    verbs = ["Reading", "Editing", "Writing", "Fetching", "Searching"]
    canonical_file = src_root / "cli" / "tui" / "presentation" / "tool" / "blocks.py"
    for verb in verbs:
        pattern = rf'["\']({re.escape(verb)})["\']'
        result = subprocess.run(
            ["grep", "-rlnE", pattern, str(src_root)],
            capture_output=True,
            text=True,
            check=False,
        )
        hits = [
            Path(line) for line in result.stdout.strip().splitlines() if line.strip()
        ]
        assert canonical_file in hits, (
            f"verb {verb!r} missing as a string literal in canonical {canonical_file}"
        )
        unexpected = [p for p in hits if p != canonical_file and p.suffix == ".py"]
        assert not unexpected, (
            f"verb {verb!r} string literal leaked outside _TOOL_VERBS "
            f"into: {unexpected}"
        )


def test_tool_verbs_table_shape() -> None:
    assert _TOOL_VERBS, "verb table must not be empty"
    for name, value in _TOOL_VERBS.items():
        assert isinstance(name, str) and name
        assert isinstance(value, tuple)
        assert len(value) == 2
        present, past = value
        assert isinstance(present, str) and present
        assert isinstance(past, str) and past


@pytest.mark.asyncio
async def test_dashboard_chat_tab_renders_verb_form_tool_block() -> None:
    from openminion.cli.tui.app import OpenMinionApp
    from openminion.cli.tui.presentation.models import ChatMessage, MessageKind
    from openminion.cli.tui.tabs.chat import ChatTab
    from openminion.cli.tui.widgets import ChatView

    app = OpenMinionApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        try:
            chat_tab = app.screen.query_one(ChatTab)
        except Exception:
            pytest.skip("dashboard ChatTab not mounted in this harness")
            return
        chat_view = chat_tab.query_one(ChatView)
        event = ToolEvent(
            tool_name="file.read",
            args={"path": "src/dashboard.py"},
            content="contents",
            duration_ms=500,
            exit_code=0,
        )
        chat_view.push_message(
            ChatMessage(
                kind=MessageKind.TOOL,
                sender="tool:file.read",
                body="src/dashboard.py",
                tool_event=event,
            )
        )
        await pilot.pause()
        rendered = ""
        for tool_block in chat_view.query(ToolBlockWidget):
            rendered = str(tool_block.query_one(".focus-tool-block-title").render())
            break
        assert "Read" in rendered, (
            f"dashboard tool-block title missing verb form; got {rendered!r}"
        )
