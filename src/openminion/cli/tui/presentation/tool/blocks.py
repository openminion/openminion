import json
from typing import Any, Literal, Mapping

from rich.text import Text
from textual.app import ComposeResult
from textual.css.query import QueryError
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static

from openminion.cli.status.tool_calls import (
    format_tool_fallback_marker,
    format_tool_provenance_marker,
)

from ..models import ToolEvent

VerbosityLevel = Literal["quiet", "normal", "verbose"]
_VERBOSE_LINE_CAP = 200
_NORMAL_LINE_CAP = 6

_TOOL_VERBS: dict[str, tuple[str, str]] = {
    "exec.run": ("Running", "Ran"),
    "file.read": ("Reading", "Read"),
    "file.edit": ("Editing", "Edited"),
    "file.write": ("Writing", "Wrote"),
}
_TOOL_VERB_PREFIXES: tuple[tuple[str, tuple[str, str]], ...] = (
    ("fetch", ("Fetching", "Fetched")),
    ("search", ("Searching", "Searched")),
)
_DEFAULT_VERBS: tuple[str, str] = ("Running", "Ran")


def verbs_for_tool(tool_name: str) -> tuple[str, str]:
    name = str(tool_name or "").strip()
    if name in _TOOL_VERBS:
        return _TOOL_VERBS[name]
    for prefix, verbs in _TOOL_VERB_PREFIXES:
        if name.startswith(prefix):
            return verbs
    return _DEFAULT_VERBS


def tool_context_hint(tool_name: str, args: Mapping[str, Any]) -> str:
    name = str(tool_name or "").strip()
    args = args or {}
    if name == "exec.run":
        return str(args.get("command", "") or "").strip()
    if name in {"file.read", "file.edit"}:
        return str(args.get("path", "") or "").strip()
    if name.startswith("fetch"):
        return str(args.get("url", "") or "").strip()
    return ""


def tool_call_body(tool_event: ToolEvent) -> str:
    hint = tool_context_hint(tool_event.tool_name, tool_event.args)
    return hint or tool_event.tool_name


class ToolBlockWidget(Widget):
    BINDINGS = [("enter", "toggle", "Toggle")]

    collapsed: reactive[bool] = reactive(False)
    verbosity: reactive[VerbosityLevel] = reactive("normal")

    can_focus = True

    EXIT_GLYPH_OK = "✓"
    EXIT_GLYPH_FAIL = "✗"
    EXIT_GLYPH_PENDING = "⏳"

    def __init__(
        self,
        tool_event: ToolEvent,
        *,
        pending: bool = False,
        verbosity: VerbosityLevel = "normal",
        **kwargs,
    ) -> None:
        super().__init__(classes="focus-tool-block", **kwargs)
        self._tool_event = tool_event
        self._pending = bool(pending)
        if verbosity in ("quiet", "normal", "verbose"):
            # type: ignore[assignment]
            self.verbosity = verbosity
        self.collapsed = self._default_collapsed_for_state()

    def compose(self) -> ComposeResult:
        yield Label(self._header_text(), classes="focus-tool-block-title")
        yield Static(self._body_renderable(), classes="focus-tool-block-body")

    def on_mount(self) -> None:
        self._refresh_widgets()

    def action_toggle(self) -> None:
        self.collapsed = not self.collapsed

    def watch_collapsed(self, _value: bool) -> None:
        self._refresh_widgets()

    def watch_verbosity(self, _value: VerbosityLevel) -> None:
        self._refresh_widgets()

    def update_event(
        self, tool_event: ToolEvent, *, pending: bool | None = None
    ) -> None:
        prev_pending = self._pending
        self._tool_event = tool_event
        if pending is not None:
            self._pending = bool(pending)
        if prev_pending and not self._pending:
            self.collapsed = self._default_collapsed_for_state()
        self._refresh_widgets()

    def _default_collapsed_for_state(self) -> bool:
        if self._pending:
            return False
        exit_code = self._tool_event.exit_code
        if exit_code is not None and exit_code != 0:
            return False
        return True

    def _exit_glyph(self) -> str:
        if self._pending:
            return self.EXIT_GLYPH_PENDING
        exit_code = self._tool_event.exit_code
        if exit_code is not None and exit_code != 0:
            return self.EXIT_GLYPH_FAIL
        return self.EXIT_GLYPH_OK

    def _refresh_widgets(self) -> None:
        try:
            if self.verbosity == "quiet":
                self.display = False
                return
            self.display = True
            self.query_one(".focus-tool-block-title", Label).update(self._header_text())
            body = self.query_one(".focus-tool-block-body", Static)
            body.display = not self.collapsed
            body.update(self._body_renderable())
        except (QueryError, AttributeError):
            pass

    def _header_text(self) -> str:
        glyph = self._exit_glyph()
        present, past = verbs_for_tool(self._tool_event.tool_name)
        verb = present if self._pending else past
        raw_hint = tool_context_hint(self._tool_event.tool_name, self._tool_event.args)
        hint = self._truncate_hint(raw_hint)
        if hint:
            head = f"{glyph} {verb} {hint}"
        else:
            head = f"{glyph} {verb} {self._tool_event.tool_name}"
        provenance_suffix = self._provenance_suffix()
        fallback_suffix = format_tool_fallback_marker(
            runtime_fallback_used=self._tool_event.runtime_fallback_used,
            runtime_fallback_chain=self._tool_event.runtime_fallback_chain,
        )
        head = f"{head}{provenance_suffix}{fallback_suffix}"
        duration = self._duration_suffix()
        if duration:
            return f"{head} · {duration}"
        if not self._pending:
            exit_code = self._tool_event.exit_code
            if exit_code is not None and exit_code != 0:
                return f"{head} · exit {exit_code}"
        return head

    def _provenance_suffix(self) -> str:
        canonical = self._tool_event.model_tool_name or self._tool_event.tool_name
        runtime = self._tool_event.runtime_tool_name
        return format_tool_provenance_marker(
            model_tool_name=canonical,
            runtime_tool_name=runtime,
            family_has_multiple_providers=bool(runtime and runtime != canonical),
        )

    def _duration_suffix(self) -> str:
        if self._pending:
            return ""
        ms = self._tool_event.duration_ms
        if ms is None:
            return ""
        try:
            ms_int = int(ms)
        except (TypeError, ValueError):
            return ""
        seconds = ms_int / 1000.0
        if seconds < 1.0:
            return f"{ms_int}ms"
        return f"{seconds:.1f}s"

    @staticmethod
    def _truncate_hint(hint: str, *, limit: int = 60) -> str:
        if not hint or len(hint) <= limit:
            return hint
        return hint[: limit - 1] + "…"

    def _body_renderable(self) -> object:
        if self.collapsed:
            return Text("")
        if self._pending and not self._tool_event.content:
            return Text("tool in progress")
        if self._tool_event.tool_name == "exec.run":
            return self._render_exec()
        if self._tool_event.tool_name == "file.read":
            return self._render_file_read()
        if self._tool_event.tool_name == "file.edit":
            return self._render_diff()
        if self._tool_event.tool_name.startswith("fetch"):
            return self._render_fetch()
        return self._render_default()

    def _render_exec(self) -> Text:
        command = str(self._tool_event.args.get("command", "") or "").strip()
        content = self._tool_event.full_content or self._tool_event.content
        lines = str(content or "").splitlines()
        cap = self._verbosity_line_cap()
        truncated = len(lines) > cap
        shown = lines[:cap] if truncated else lines
        text = Text()
        if command:
            text.append(f"$ {command}\n", style="bold")
        text.append("\n".join(shown) if shown else "(no output)")
        if truncated:
            text.append("\n... show more", style="dim")
        return text

    def _verbosity_line_cap(self) -> int:
        if self.verbosity == "quiet":
            return 0
        if self.verbosity == "verbose":
            return _VERBOSE_LINE_CAP
        return _NORMAL_LINE_CAP

    def _render_file_read(self) -> Text:
        content = self._tool_event.full_content or self._tool_event.content
        text = Text()
        for index, line in enumerate(str(content or "").splitlines(), start=1):
            text.append(f"{index:>3} | ", style="dim")
            text.append(f"{line}\n")
        if not text.plain:
            text.append("(empty file)")
        return text

    def _render_diff(self) -> Text:
        content = self._tool_event.full_content or self._tool_event.content
        added_color, removed_color = self._diff_colors()
        text = Text()
        for line in str(content or "").splitlines():
            style = ""
            if line.startswith("+"):
                style = added_color
            elif line.startswith("-"):
                style = removed_color
            elif line.startswith("@@"):
                style = "bold"
            text.append(f"{line}\n", style=style)
        if not text.plain:
            text.append("(empty diff)")
        return text

    def _diff_colors(self) -> tuple[str, str]:
        try:
            theme = self._active_theme()
            if theme is not None:
                return (str(theme.state_ok), str(theme.state_error))
        except Exception:
            pass
        return ("green", "red")

    def _active_theme(self):
        try:
            app = self.app
        except AttributeError:
            app = None
        if app is not None:
            theme = getattr(app, "active_theme", None)
            if theme is not None:
                return theme
        try:
            from openminion.cli.theme import DARK

            return DARK
        except ImportError:
            return None

    def _render_fetch(self) -> Text:
        url = str(self._tool_event.args.get("url", "") or "").strip()
        text = Text()
        if url:
            text.append(f"{url}\n", style="bold")
        text.append(str(self._tool_event.content or "(empty response)"))
        return text

    def _render_default(self) -> Text:
        text = Text()
        if self._tool_event.args:
            try:
                text.append(json.dumps(self._tool_event.args, sort_keys=True))
            except (TypeError, ValueError):
                text.append(str(self._tool_event.args))
            text.append("\n")
        text.append(str(self._tool_event.content or "(no content)"))
        return text


__all__ = ("ToolBlockWidget", "tool_call_body", "tool_context_hint")
