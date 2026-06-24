from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.tui.terminal.streaming import _render_tool_block
from openminion.cli.tui.presentation.models import ToolEvent


def _render(renderable) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    console.print(renderable)
    return buf.getvalue()


def _event(**overrides) -> ToolEvent:
    base: dict = {
        "tool_name": "web.search",
        "args": {"query": "latest python release"},
        "content": "result snippet",
        "duration_ms": 600,
        "exit_code": 0,
    }
    base.update(overrides)
    return ToolEvent(**base)


def test_provenance_suffix_renders_when_runtime_differs_from_canonical() -> None:

    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
    )
    out = _render(_render_tool_block(event))
    assert "→ serper" in out


def test_provenance_suffix_absent_when_runtime_matches_canonical() -> None:

    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="web.search",
    )
    out = _render(_render_tool_block(event))
    assert "→" not in out


def test_provenance_suffix_absent_when_runtime_unset() -> None:

    event = _event(model_tool_name="web.search", runtime_tool_name="")
    out = _render(_render_tool_block(event))
    assert "→" not in out


def test_fallback_marker_renders_with_chain_first_attempt_label() -> None:

    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        runtime_fallback_used=True,
        runtime_fallback_chain=["search.tavily.search"],
    )
    out = _render(_render_tool_block(event))
    assert "fallback after tavily" in out
    # Provenance also still renders.
    assert "→ serper" in out


def test_fallback_marker_renders_bare_when_chain_empty() -> None:

    event = _event(
        runtime_fallback_used=True,
        runtime_fallback_chain=None,
    )
    out = _render(_render_tool_block(event))
    assert "(fallback)" in out


def test_fallback_marker_absent_when_not_used() -> None:

    event = _event(runtime_fallback_used=False)
    out = _render(_render_tool_block(event))
    assert "fallback" not in out.lower()


def test_existing_failure_suffix_still_appears_with_provenance() -> None:

    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        exit_code=1,
    )
    out = _render(_render_tool_block(event))
    assert "→ serper" in out
    assert "exit 1" in out


def test_no_markers_for_legacy_tool_event_default_construction() -> None:

    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "ls -la"},
        content="file1\nfile2",
    )
    out = _render(_render_tool_block(event))
    assert "→" not in out
    assert "fallback" not in out.lower()
