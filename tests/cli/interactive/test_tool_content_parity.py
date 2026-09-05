from __future__ import annotations

import io

from rich.console import Console

from openminion.cli.interactive.terminal.streaming import _render_tool_block
from openminion.cli.presentation.models import ToolEvent
from openminion.cli.presentation.tool.blocks import ToolBlockWidget


def _terminal_text(event: ToolEvent) -> str:
    output = io.StringIO()
    Console(file=output, force_terminal=False, width=120).print(
        _render_tool_block(event)
    )
    return output.getvalue()


def test_tool_headers_preserve_shared_failure_facts() -> None:
    event = ToolEvent(
        tool_name="web.search",
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        runtime_fallback_used=True,
        runtime_fallback_chain=["search.tavily.search"],
        args={"query": "release notes"},
        content="failed",
        duration_ms=1200,
        exit_code=7,
    )

    terminal = _terminal_text(event)
    rich = ToolBlockWidget(event, pending=False)._header_text()

    for fact in ("Searched the web.", "1s", "exit 7", "serper", "tavily"):
        assert fact in terminal
        assert fact in rich


def test_canonical_edit_tool_uses_diff_body_in_both_renderers() -> None:
    event = ToolEvent(
        tool_name="file.edit",
        args={"path": "example.py"},
        content="@@ -1 +1 @@\n-old\n+new",
        exit_code=0,
    )

    terminal = _terminal_text(event)
    rich_widget = ToolBlockWidget(event, pending=False)
    rich_widget.collapsed = False
    rich = rich_widget._body_renderable().plain

    assert "-old" in terminal and "+new" in terminal
    assert "-old" in rich and "+new" in rich


def test_plain_write_result_remains_plain_in_both_renderers() -> None:
    event = ToolEvent(
        tool_name="file.write",
        args={"path": "example.py"},
        content="wrote 12 bytes",
        exit_code=0,
    )

    terminal = _terminal_text(event)
    rich_widget = ToolBlockWidget(event, pending=False)
    rich_widget.collapsed = False
    rich = rich_widget._body_renderable().plain

    assert "wrote 12 bytes" in terminal
    assert "wrote 12 bytes" in rich
    assert "(empty diff)" not in rich
