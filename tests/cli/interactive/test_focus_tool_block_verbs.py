from __future__ import annotations

import re
from pathlib import Path

import pytest

from openminion.cli.presentation.models import ToolEvent
from openminion.cli.presentation.tool.blocks import (
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
    src_root = Path(__file__).resolve().parents[3] / "src" / "openminion"
    verbs = ["Reading", "Editing", "Writing", "Fetching", "Searching"]
    canonical_file = src_root / "cli" / "presentation" / "tool" / "blocks.py"
    for verb in verbs:
        pattern = rf'["\']({re.escape(verb)})["\']'
        compiled = re.compile(pattern)
        hits = [
            path
            for path in src_root.rglob("*.py")
            if compiled.search(path.read_text(encoding="utf-8"))
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
