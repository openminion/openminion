from __future__ import annotations

from openminion.cli.presentation.models import ToolEvent
from openminion.cli.presentation.tool.blocks import ToolBlockWidget


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


def test_widget_header_provenance_suffix_when_runtime_differs() -> None:
    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "→ serper" in title


def test_widget_header_provenance_suffix_absent_when_runtime_matches() -> None:
    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="web.search",
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "→" not in title


def test_widget_header_fallback_marker_with_chain() -> None:
    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        runtime_fallback_used=True,
        runtime_fallback_chain=["search.tavily.search"],
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "fallback after tavily" in title


def test_widget_header_fallback_marker_bare_when_chain_empty() -> None:
    event = _event(runtime_fallback_used=True, runtime_fallback_chain=None)
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "(fallback)" in title


def test_widget_header_no_markers_when_neither_set() -> None:
    event = ToolEvent(
        tool_name="exec.run",
        args={"command": "ls -la"},
        content="file1\nfile2",
        duration_ms=120,
        exit_code=0,
    )
    title = ToolBlockWidget(event, pending=False)._header_text()
    assert "→" not in title
    assert "fallback" not in title.lower()


def test_widget_header_pending_state_still_appends_markers() -> None:
    event = _event(
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        runtime_fallback_used=True,
        runtime_fallback_chain=["search.tavily.search"],
    )
    title = ToolBlockWidget(event, pending=True)._header_text()
    assert "→ serper" in title
    assert "fallback after tavily" in title
