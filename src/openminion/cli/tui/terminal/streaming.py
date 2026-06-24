from __future__ import annotations

import re
import time
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text

from openminion.cli.presentation.styles import StyleToken
from openminion.cli.status.tool_calls import (
    format_tool_fallback_marker,
    format_tool_provenance_marker,
)
from openminion.cli.tui.presentation.markers import (
    MARKER_TOOL_FAIL,
    MARKER_TOOL_OK,
    MARKER_ASSISTANT,
    MARKER_TOOL_RUNNING,
    marker_text,
    token_rich_style,
)
from openminion.cli.tui.presentation.models import ToolEvent

from .spinner import THINKING_VERB, Spinner, format_status_row


def _looks_like_markdown(text: str) -> bool:
    sample = text.strip()
    return bool(
        sample.startswith(("#", "- ", "* ", "> ", "```", "1.", "|")) or "```" in sample
    )


def _looks_like_unified_diff(text: str) -> bool:
    if not text:
        return False
    lines = text.split("\n")
    for line in lines[:3]:
        if line.startswith("$ "):
            return False
    has_hunk = any(_HUNK_HEADER_RE.match(line) for line in lines)
    if not has_hunk:
        return False
    has_plus = False
    has_minus = False
    for line in lines:
        if line.startswith("+++"):
            continue
        if line.startswith("---"):
            continue
        if line.startswith("+"):
            has_plus = True
        elif line.startswith("-"):
            has_minus = True
        if has_plus and has_minus:
            break
    return has_plus and has_minus


_STREAM_CURSOR = "▍"
_BOUNDED_FALLBACK_THRESHOLD_S = 0.05
_LIVE_REFRESH_PER_SECOND = 4
_TOOL_BLOCK_TRUNCATE_LINES = 6
_TOOL_BLOCK_VERBOSE_MAX_LINES = 200

_ASSISTANT_MARKER = "⏺"
_TOOL_MARKER = "●"
_DIFF_RENDER_TOOL_NAMES = frozenset({"Edit", "Write"})
_HUNK_HEADER_RE = re.compile(r"^@@\s+-\d+(,\d+)?\s+\+\d+(,\d+)?\s+@@")


class TerminalTurnHandle:
    def __init__(self, console: Console, *, plain: bool = False) -> None:
        self._console = console
        self._buffer = ""
        self._started_at: float = 0.0
        self._live: Live | None = None
        self._completed = False
        self._plain = bool(plain)
        self._spinner: Spinner | None = None
        self._in_thinking_frame = True
        self._active_tool: dict[str, Any] | None = None
        self._status_label = ""

    def set_status_label(self, label: str) -> None:
        self._status_label = str(label or "").strip()
        if self._live is not None:
            try:
                self._live.update(self._render(), refresh=True)
            except Exception:
                return

    def set_active_tool(
        self,
        *,
        call_id: str,
        tool_name: str,
        args: dict[str, Any],
        started_at: float,
    ) -> None:
        self._active_tool = {
            "call_id": str(call_id or ""),
            "tool_name": str(tool_name or ""),
            "args": dict(args or {}),
            "started_at": float(started_at),
        }
        if self._live is not None:
            try:
                self._live.update(self._render(), refresh=True)
            except Exception:
                return

    def clear_active_tool(self, call_id: str = "") -> None:
        if self._active_tool is None:
            return
        if call_id:
            active_id = str(self._active_tool.get("call_id", "") or "")
            if active_id and active_id != str(call_id):
                return
        self._active_tool = None
        if self._live is not None:
            try:
                self._live.update(self._render(), refresh=True)
            except Exception:
                return

    def has_active_tool(self) -> bool:
        return self._active_tool is not None

    def start(self) -> "TerminalTurnHandle":
        self._started_at = time.monotonic()
        self._spinner = Spinner(self._started_at, plain=self._plain)
        self._live = Live(
            self._render(),
            console=self._console,
            transient=False,
            refresh_per_second=_LIVE_REFRESH_PER_SECOND,
            auto_refresh=True,
        )
        self._live.start(refresh=True)
        return self

    def append_token(self, s: str) -> None:
        if self._completed:
            return
        if not s:
            return
        if self._in_thinking_frame:
            self._in_thinking_frame = False
        self._buffer += s
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def append_tool_block(self, event: ToolEvent) -> None:
        if self._live is not None:
            self._live.stop()
            self._console.print(_render_tool_block(event))
            self._live.start(refresh=True)
        else:
            self._console.print(_render_tool_block(event))

    def complete(self, final_text: str | None = None) -> None:
        if self._completed:
            return
        if final_text is not None:
            self._buffer = final_text
        elapsed = time.monotonic() - self._started_at
        is_bounded_fallback = elapsed <= _BOUNDED_FALLBACK_THRESHOLD_S
        if self._live is not None:
            if is_bounded_fallback:
                self._live.update(Text(self._buffer or ""), refresh=True)
            else:
                final_renderable = self._render(
                    force_no_cursor=True, force_no_status=True
                )
                buffer = self._buffer or ""
                if buffer and _looks_like_markdown(buffer):
                    marker = marker_text(MARKER_ASSISTANT, bold=True)
                    marker.append(" ")
                    md = RichMarkdown(
                        buffer,
                        code_theme="monokai",
                        inline_code_lexer="text",
                        justify="left",
                    )
                    final_renderable = Group(marker, md)
                self._live.update(final_renderable, refresh=True)
            self._live.stop()
            self._live = None
        self._console.print()
        self._completed = True

    def _render(
        self,
        *,
        force_no_cursor: bool = False,
        force_no_status: bool = False,
    ) -> Any:
        body_row = Text()
        body_row.append_text(marker_text(MARKER_ASSISTANT, bold=True))
        body_row.append(" ")
        body_row.append(self._buffer or "")
        if not force_no_cursor and not self._completed:
            body_row.append(_STREAM_CURSOR, style="dim")

        running_block: Any = None
        if self._active_tool is not None:
            elapsed = max(
                0.0, time.monotonic() - float(self._active_tool["started_at"])
            )
            running_block = _render_in_progress_tool_block(
                self._active_tool["tool_name"],
                self._active_tool["args"],
                elapsed_seconds=elapsed,
            )

        if force_no_status:
            if running_block is not None:
                return Group(running_block, body_row)
            return body_row

        now = time.monotonic()
        if self._spinner is None:
            if running_block is not None:
                return Group(running_block, body_row)
            return body_row
        if self._in_thinking_frame:
            verb = "" if self._plain else THINKING_VERB
        else:
            verb = self._spinner.current_verb(now)
        elapsed_label = self._spinner.elapsed_label(now)
        status_row = format_status_row(
            verb,
            elapsed_label,
            "esc to interrupt",
            plain=self._plain,
            status_label=self._status_label,
            spinner_frame=self._spinner.current_frame(now),
        )
        if running_block is not None:
            return Group(running_block, body_row, status_row)
        return Group(body_row, status_row)


def _render_tool_block(event: ToolEvent) -> Group:
    body_for_detection = event.full_content or event.content or ""
    if event.tool_name in _DIFF_RENDER_TOOL_NAMES and _looks_like_unified_diff(
        body_for_detection
    ):
        return _render_diff_block(event)
    return _render_plain_tool_block(
        event,
        cap=_TOOL_BLOCK_TRUNCATE_LINES,
        include_event_markers=True,
        hint_style="dim italic",
    )


def _render_full_tool_block(event: ToolEvent, *, cap: int | None = None) -> Group:
    body_for_detection = event.full_content or event.content or ""
    if event.tool_name in _DIFF_RENDER_TOOL_NAMES and _looks_like_unified_diff(
        body_for_detection
    ):
        return _render_diff_block(event, cap=cap)
    return _render_plain_tool_block(
        event,
        cap=cap,
        include_event_markers=False,
        hint_style="dim",
    )


def _render_plain_tool_block(
    event: ToolEvent,
    *,
    cap: int | None,
    include_event_markers: bool,
    hint_style: str,
) -> Group:
    body_text = event.full_content or event.content or ""
    return Group(
        _tool_title_row(event, include_event_markers=include_event_markers),
        _collapsed_body_row(body_text, cap=cap, hint_style=hint_style),
    )


def _diff_line_style(line: str) -> str:
    if line.startswith("+++") or line.startswith("---"):
        return "bold"
    if line.startswith("+"):
        return "green"
    if line.startswith("-"):
        return "red"
    if line.startswith("@@"):
        return "cyan"
    if line.startswith("diff --git ") or line.startswith("index "):
        return "dim"
    return ""


def _render_diff_block(
    event: ToolEvent, *, cap: int | None = _TOOL_BLOCK_TRUNCATE_LINES
) -> Group:
    title_row = _tool_title_row(event, include_event_markers=False)
    body_text = (event.full_content or event.content or "").rstrip()
    if not body_text:
        body_text = "(no output)"
    lines = body_text.split("\n")
    body_row = Text()
    if cap is not None and len(lines) > cap:
        visible = lines[:cap]
        omitted = len(lines) - cap
        for line in visible:
            style = _diff_line_style(line)
            body_row.append(f"  {line}\n", style=style)
        body_row.append(
            f"  … +{omitted} lines (use /expand to see all)\n",
            style="dim italic",
        )
    else:
        for line in lines:
            style = _diff_line_style(line)
            body_row.append(f"  {line}\n", style=style)

    return Group(title_row, body_row)


def _tool_title_row(event: ToolEvent, *, include_event_markers: bool) -> Text:
    exit_code = event.exit_code
    is_ok = exit_code in (None, 0)
    title_row = Text()
    title_row.append_text(
        marker_text(MARKER_TOOL_OK if is_ok else MARKER_TOOL_FAIL, bold=True)
    )
    title_row.append(" ")
    title_row.append(_verb_form_title(event), style="bold")
    if include_event_markers:
        title_row.append(_tool_event_markers(event))
    if exit_code is not None and exit_code != 0:
        title_row.append(
            f" ✗ (exit {exit_code})",
            style=token_rich_style(StyleToken.ERROR, bold=True),
        )
    return title_row


def _collapsed_body_row(body_text: str, *, cap: int | None, hint_style: str) -> Text:
    from openminion.cli.status.activity_ledger import collapse_output

    collapsed = collapse_output(
        body_text,
        max_lines=cap if cap is not None else 10**9,
    )
    body_row = Text()
    for line in collapsed.visible_lines:
        body_row.append(f"  {line}\n")
    if collapsed.truncated:
        body_row.append(f"  {collapsed.expand_hint}\n", style=hint_style)
    return body_row


def _tool_event_markers(event: ToolEvent) -> str:
    canonical = event.model_tool_name or event.tool_name
    runtime = event.runtime_tool_name
    provenance = format_tool_provenance_marker(
        model_tool_name=canonical,
        runtime_tool_name=runtime,
        family_has_multiple_providers=bool(runtime and runtime != canonical),
    )
    fallback = format_tool_fallback_marker(
        runtime_fallback_used=event.runtime_fallback_used,
        runtime_fallback_chain=event.runtime_fallback_chain,
    )
    return f"{provenance}{fallback}"


def _verb_form_title(event: ToolEvent) -> str:
    name = (event.tool_name or "tool").strip() or "tool"
    args = dict(event.args or {})
    if not args:
        return name
    # Tool-family arg picker.
    arg_value: Any = None
    for key in ("cmd", "command", "path", "file", "query", "pattern", "url"):
        if key in args:
            arg_value = args[key]
            break
    if arg_value is None:
        # Fall back to the first arg value.
        try:
            arg_value = next(iter(args.values()))
        except StopIteration:
            arg_value = None
    if arg_value is None:
        return name
    arg_str = str(arg_value).strip()
    if len(arg_str) > 60:
        arg_str = arg_str[:57] + "..."
    return f"{name}({arg_str})"


def _body_line_count(event: ToolEvent) -> int:
    body = (event.full_content or event.content or "").rstrip()
    if not body:
        return 0
    return body.count("\n") + 1


def _format_elapsed_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def _render_in_progress_tool_block(
    tool_name: str,
    args: dict[str, Any] | None = None,
    elapsed_seconds: float = 0.0,
) -> Group:
    synthetic = ToolEvent(
        tool_name=tool_name,
        args=dict(args or {}),
        content="",
        full_content="",
    )
    verb = _verb_form_title(synthetic)

    title_row = Text()
    title_row.append_text(marker_text(MARKER_TOOL_RUNNING, bold=True))
    title_row.append(" ")
    title_row.append("Running ", style="bold")
    title_row.append(verb, style="bold")
    if elapsed_seconds > 0.0:
        title_row.append(
            f" · {_format_elapsed_seconds(elapsed_seconds)}",
            style="dim",
        )

    return Group(title_row)


def is_truncated(event: ToolEvent) -> bool:
    return _body_line_count(event) > _TOOL_BLOCK_TRUNCATE_LINES


__all__ = [
    "TerminalTurnHandle",
    "_render_full_tool_block",
    "_render_tool_block",
    "is_truncated",
]
