from collections.abc import Callable, Iterable
import logging
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, is_done
from prompt_toolkit.formatted_text import ANSI, FormattedText, to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    FloatContainer,
    HSplit,
    VerticalAlign,
    Window,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.renderer import CPR_Support
from prompt_toolkit.styles import Style

from openminion.cli.presentation.animation import default_animation_registry
from openminion.cli.presentation.animation.models import (
    AnimationResolution,
    AnimationSpec,
)
from openminion.cli.presentation.styles import (
    StyleToken,
    active_theme_color,
    is_color_enabled,
)
from openminion.cli.ux.input_normalization import normalize_multiline_input_text


_LOGGER = logging.getLogger(__name__)
_PROMPT_FRESH = "❯ "
_PROMPT_RESUMED = "↳ "
_PROMPT_DISABLED = "… "
_PROMPT_BUSY = "❯ "
_COMPLETION_MENU_ROWS = 10
_PLACEHOLDER_IDLE = "Ask anything · @ to mention a file · / for commands"
_PLACEHOLDER_BUSY = "Type to queue for the next turn · Esc interrupts"
_SLASH_NAME_CHARS = tuple("abcdefghijklmnopqrstuvwxyz0123456789-_")
_PHASE_ANIMATIONS = {
    "clarifying": "focusbeam",
    "analyzing": "braillewave",
    "planning": "assemble",
    "awaiting_plan_review": "slowbreath",
    "awaiting_confirmation": "pulse",
    "executing": "gearspin",
    "replanning": "dna",
    "reviewing": "orbitnodes",
    "verifying": "scanline",
    "evaluating_completion": "fillsweep",
    "saving_context": "cascade",
    "waiting_for_user": "breathe",
    "blocked": "warningpulse",
    "error": "warningpulse",
    "working": "sparkle",
}


def _focus_prompt_style(*, color: bool = True) -> Style:
    if not color:
        return Style.from_dict(
            {
                "bottom-toolbar": "noreverse",
                "bottom-toolbar.text": "noreverse",
                "placeholder": "noreverse",
            }
        )
    from openminion.cli.presentation.styles import get_active_theme_name
    from openminion.cli.theme import DARK, lookup_theme

    theme = lookup_theme(get_active_theme_name()) or DARK
    toolbar = f"noreverse bg:{theme.surface_panel_bg} {theme.text_secondary}"
    placeholder = f"noreverse bg:{theme.surface_app_bg} {theme.text_secondary}"
    return Style.from_dict(
        {
            "bottom-toolbar": toolbar,
            "bottom-toolbar.text": toolbar,
            "busy-indicator": active_theme_color(StyleToken.SPINNER),
            "placeholder": placeholder,
            "completion-menu": (
                f"noreverse bg:{theme.surface_panel_bg} {theme.text_primary}"
            ),
            "completion-menu.completion.current": (
                f"noreverse bg:{theme.surface_divider} {theme.text_primary}"
            ),
        }
    )


def edit_external_draft(text: str, editor_command: str) -> str:
    argv = shlex.split(editor_command)
    if not argv:
        raise ValueError("VISUAL or EDITOR is empty")
    with tempfile.TemporaryDirectory(prefix="openminion-editor-") as temp_dir:
        draft_path = Path(temp_dir) / "draft.md"
        draft_path.write_text(text, encoding="utf-8")
        subprocess.run([*argv, str(draft_path)], check=True)
        edited = draft_path.read_text(encoding="utf-8")
    return edited


def _completion_menu_is_open() -> bool:
    return get_app().current_buffer.complete_state is not None


def _call_safely(callback: object) -> None:
    if not callable(callback):
        return
    try:
        callback()
    except Exception:
        _LOGGER.debug("terminal callback failed", exc_info=True)


class _ClickableCompletionMenuControl(CompletionsMenuControl):
    """Vertical completion menu that applies clicked entries."""

    def mouse_handler(self, mouse_event: MouseEvent) -> object:
        if mouse_event.event_type != MouseEventType.MOUSE_UP:
            return super().mouse_handler(mouse_event)

        buffer = get_app().current_buffer
        state = buffer.complete_state
        if state is None:
            return None

        index = mouse_event.position.y
        if 0 <= index < len(state.completions):
            buffer.apply_completion(state.completions[index])
        return None


def _configure_completion_menu(session: PromptSession[str]) -> None:
    """Configure click-only tracking and click-to-apply completion entries."""

    _use_click_only_mouse_tracking(session)

    seen: set[int] = set()

    def visit(node: object) -> None:
        node_id = id(node)
        if node_id in seen:
            return
        seen.add(node_id)

        if (
            isinstance(node, Window)
            and isinstance(node.content, CompletionsMenuControl)
            and not isinstance(node.content, _ClickableCompletionMenuControl)
        ):
            node.content = _ClickableCompletionMenuControl()

        content = getattr(node, "content", None)
        if content is not None:
            visit(content)

        alternative = getattr(node, "alternative_content", None)
        if alternative is not None:
            visit(alternative)

        for child in getattr(node, "children", ()) or ():
            visit(child)

        for float_item in getattr(node, "floats", ()) or ():
            visit(getattr(float_item, "content", None))

    try:
        visit(session.layout.container)
    except Exception:
        _LOGGER.debug("completion menu customization failed", exc_info=True)
        return


def _configure_flow_input_layout(session: PromptSession[str]) -> None:
    """Keep the composer immediately after the transcript, without a spacer."""

    root = session.layout.container
    input_window = session.layout.current_window
    if not isinstance(root, HSplit) or not isinstance(input_window, Window):
        raise RuntimeError("prompt layout does not expose the expected input stack")
    main_input = root.children[0]
    if not isinstance(main_input, ConditionalContainer) or not isinstance(
        main_input.alternative_content, FloatContainer
    ):
        raise RuntimeError("prompt layout does not expose the expected menu stack")
    input_stack = main_input.alternative_content.content
    if not isinstance(input_stack, HSplit):
        raise RuntimeError("prompt layout does not expose the expected input stack")
    toolbar = root.children[-1]
    if not isinstance(toolbar, ConditionalContainer):
        raise RuntimeError("prompt layout does not expose the expected footer")

    root.align = VerticalAlign.TOP
    input_window.dont_extend_height = Condition(lambda: True)
    input_window.height = Dimension()
    input_stack.children.append(
        Window(
            height=lambda: Dimension.exact(
                _COMPLETION_MENU_ROWS
                if session.default_buffer.complete_state is not None
                else 0
            ),
            dont_extend_height=True,
        )
    )
    # prompt-toolkit normally hides its toolbar until CPR establishes the
    # remaining terminal height. This inline layout does not need CPR.
    toolbar.filter = Condition(lambda: session.bottom_toolbar is not None) & ~is_done


def _use_click_only_mouse_tracking(session: PromptSession[str]) -> None:
    """Request clicks without the all-motion mode unused by the completion menu."""

    output = session.app.output
    if not isinstance(output, Vt100_Output):
        return

    def enable_click_support() -> None:
        output.write_raw("\x1b[?1000h")
        output.write_raw("\x1b[?1006h")

    setattr(output, "enable_mouse_support", enable_click_support)


class _SlashAndAtCompleter(Completer):
    """Completer that fires on `/` (slash commands) or `@` (paths)."""

    def __init__(
        self,
        slash_commands: Iterable[str],
        path_completer: Completer | None = None,
    ) -> None:
        self._slashes = sorted({str(name) for name in slash_commands})
        self._path_completer = path_completer

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/help "):
            prefix = text.removeprefix("/help ")
            if " " in prefix:
                return
            match_prefix = prefix if prefix.startswith("/") else f"/{prefix}"
            for slash in self._slashes:
                if slash.startswith(match_prefix):
                    replacement = slash if prefix.startswith("/") else slash[1:]
                    yield Completion(
                        replacement,
                        start_position=-len(prefix),
                        display=slash,
                    )
            return
        if text.startswith("/"):
            for slash in self._slashes:
                if slash.startswith(text):
                    yield Completion(
                        slash,
                        start_position=-len(text),
                        display=slash,
                    )
            return
        at_pos = text.rfind("@")
        if at_pos >= 0 and self._path_completer is not None:
            from prompt_toolkit.document import Document

            sub_doc = Document(text=text[at_pos + 1 :])
            for c in self._path_completer.get_completions(sub_doc, complete_event):
                yield Completion(
                    "@" + c.text,
                    start_position=c.start_position - 1,
                    display=c.display,
                    display_meta=c.display_meta,
                )


class TerminalComposer:
    """Prompt-toolkit input region for the default interactive CLI."""

    def __init__(
        self,
        *,
        slash_commands: Iterable[str] = (),
        bottom_toolbar: object = None,
        active_status: Callable[[], str] | None = None,
        history_file: str | None = None,
        on_ctrl_o: object = None,
        on_shift_tab: object = None,
        on_escape: Callable[[], None] | None = None,
        editor_command: str = "",
        on_editor_error: Callable[[str], None] | None = None,
        working_dir: str | None = None,
        animation: AnimationResolution | None = None,
        progress: str = "full",
        color: bool | None = None,
    ) -> None:
        self._on_escape = on_escape
        self._editor_command = str(editor_command or "").strip()
        self._on_editor_error = on_editor_error
        self._next_draft: str | None = None
        path = PathCompleter(only_directories=False)
        self._completer = _SlashAndAtCompleter(slash_commands, path)
        self._is_resumed = False
        self._disabled = False
        self._busy = False
        self._busy_started_at = 0.0
        self._animation_registry = default_animation_registry()
        if animation is None:
            animation = self._animation_registry.resolve(
                "openminion", "braille", source="default"
            )
        self._semantic_animation = animation.source == "default"
        self._activity_animation = ""
        self._set_animation(animation.spec)
        self._progress = progress
        self._color = is_color_enabled() if color is None else color
        self._multiline = False
        self._bottom_toolbar = bottom_toolbar
        self._active_status = active_status
        self._working_dir = working_dir
        kb = KeyBindings()

        @kb.add("c-j")
        def _(event):
            self._insert_newline(event)

        @kb.add("enter")
        def _(event):
            self._insert_newline(event)

        @kb.add("/")
        def _(event):
            self._insert_slash(event)

        for char in _SLASH_NAME_CHARS:
            kb.add(char)(self._insert_slash_name_char)

        kb.add("backspace")(self._delete_before_cursor)
        kb.add("<bracketed-paste>")(self._handle_bracketed_paste)

        if callable(on_ctrl_o):

            @kb.add("c-o")
            def _ctrl_o(event) -> None:
                _call_safely(on_ctrl_o)

        if callable(on_shift_tab):

            @kb.add("s-tab")
            def _shift_tab(event) -> None:
                _call_safely(on_shift_tab)

        if callable(on_escape):

            @kb.add("escape")
            def _escape(event: Any) -> None:
                _call_safely(self._on_escape)
                event.app.invalidate()

        kb.add("c-x", "c-e")(self._launch_editor)

        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(history_file) if history_file else None,
            key_bindings=kb,
            enable_history_search=True,
            erase_when_done=True,
            mouse_support=Condition(_completion_menu_is_open),
            reserve_space_for_menu=_COMPLETION_MENU_ROWS,
            style=_focus_prompt_style(color=self._color),
        )
        output = self._session.app.output
        if isinstance(output, Vt100_Output):
            self._session.app.renderer.cpr_support = CPR_Support.NOT_SUPPORTED
        _configure_completion_menu(self._session)
        _configure_flow_input_layout(self._session)

    def apply_theme(self) -> None:
        if not self._color:
            return
        style = _focus_prompt_style()
        self._session.style = style
        app = getattr(self._session, "app", None)
        if app is not None:
            app.style = style
        self.invalidate()

    def set_resumed(self, is_resumed: bool) -> None:
        self._is_resumed = bool(is_resumed)

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = bool(disabled)

    def set_busy(self, busy: bool) -> None:
        is_busy = bool(busy)
        if is_busy and not self._busy:
            self._busy_started_at = time.monotonic()
            self.set_activity("working")
        self._busy = is_busy
        self._session.app.erase_when_done = True
        self.invalidate()

    def set_activity(self, status_key: str) -> None:
        if not self._semantic_animation:
            return
        animation_name = _PHASE_ANIMATIONS.get(status_key, "sparkle")
        if animation_name == self._activity_animation:
            return
        resolution = self._animation_registry.resolve(
            "unicode",
            animation_name,
            source="phase",
            discover=True,
            allow_fallback=True,
        )
        self._activity_animation = animation_name
        self._set_animation(resolution.spec)
        self._busy_started_at = time.monotonic()
        self.invalidate()

    def _set_animation(self, animation: AnimationSpec) -> None:
        self._animation_frames: tuple[str, ...] = animation.frames
        self._animation_interval_ms: int = animation.interval_ms

    def focus_input(self) -> None:
        pass

    def invalidate(self) -> None:
        app = getattr(self._session, "app", None)
        invalidator = getattr(app, "invalidate", None)
        if callable(invalidator):
            invalidator()

    @property
    def prompt_session(self) -> PromptSession[str]:
        return self._session

    def toggle_multiline(self) -> None:
        self._multiline = not self._multiline

    def _launch_editor(self, event: Any) -> None:
        event.app.create_background_task(self._edit_live_draft(event))

    def prefill_draft(self, text: str) -> None:
        self._next_draft = str(text or "")

    async def edit_draft(self, text: str) -> str | None:
        if not self._editor_command:
            self._report_editor_error("set VISUAL or EDITOR to choose an editor")
            return None
        try:
            return await run_in_terminal(
                lambda: edit_external_draft(text, self._editor_command),
                in_executor=True,
            )
        except (
            OSError,
            UnicodeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as exc:
            self._report_editor_error(str(exc))
            return None

    async def _edit_live_draft(self, event: Any) -> None:
        buffer = event.app.current_buffer
        edited = await self.edit_draft(buffer.text)
        if edited is not None:
            buffer.document = Document(text=edited, cursor_position=len(edited))

    def _report_editor_error(self, message: str) -> None:
        if self._on_editor_error is not None:
            self._on_editor_error(message)

    def _insert_newline(self, event) -> None:
        if not self._multiline:
            if not event.app.current_buffer.text.strip():
                return
            event.app.current_buffer.validate_and_handle()
            return
        event.app.current_buffer.insert_text("\n")

    def _insert_slash(self, event) -> None:
        buffer = event.app.current_buffer
        buffer.insert_text("/")
        self._refresh_slash_completion(buffer)

    def _insert_slash_name_char(self, event) -> None:
        buffer = event.app.current_buffer
        buffer.insert_text(str(getattr(event, "data", "") or ""))
        if buffer.document.text_before_cursor.startswith("/"):
            self._refresh_slash_completion(buffer)

    def _delete_before_cursor(self, event) -> None:
        buffer = event.app.current_buffer
        buffer.delete_before_cursor(count=1)
        if buffer.document.text_before_cursor.startswith("/"):
            self._refresh_slash_completion(buffer)

    def _refresh_slash_completion(self, buffer) -> None:
        try:
            buffer.start_completion(select_first=False)
        except Exception:
            _LOGGER.debug("completion refresh failed", exc_info=True)

    def _apply_pasted_text(self, text: str, *, buffer) -> None:
        text = normalize_multiline_input_text(text)
        if not text:
            return
        from pathlib import Path as _Path

        from openminion.cli.presentation.image_paste import (
            detect_image_path,
            format_image_reference,
        )

        working = _Path(self._working_dir) if self._working_dir else None
        detected = detect_image_path(text, working_dir=working)
        if detected is not None:
            buffer.insert_text(format_image_reference(detected, working_dir=working))
            return
        if "\n" in text and not self._multiline:
            self._multiline = True
        buffer.insert_text(text)

    def _handle_bracketed_paste(self, event) -> None:
        self._apply_pasted_text(
            str(getattr(event, "data", "") or ""),
            buffer=event.app.current_buffer,
        )

    async def read_line(self) -> str:
        if self._disabled:
            raise RuntimeError("composer disabled — refuse to read input")
        self.apply_theme()
        draft = self._next_draft or ""
        if "\n" in draft:
            self._multiline = True
        # Historical guard: patch_stdout(raw=True)
        with patch_stdout():
            try:
                text = await self._session.prompt_async(
                    self._formatted_prompt,
                    completer=self._completer,
                    complete_while_typing=True,
                    multiline=Condition(lambda: self._multiline),
                    bottom_toolbar=self._formatted_bottom_toolbar,
                    placeholder=self._formatted_placeholder,
                    refresh_interval=self._prompt_refresh_interval(),
                    default=draft,
                )
                self._next_draft = None
            finally:
                self._multiline = False
        return str(text or "").rstrip("\n")

    def _formatted_bottom_toolbar(self):
        if self._bottom_toolbar is None:
            return None
        value = (
            self._bottom_toolbar()
            if callable(self._bottom_toolbar)
            else self._bottom_toolbar
        )
        if isinstance(value, str):
            if not value.strip():
                return None
            return ANSI(value)
        return value

    def _formatted_placeholder(self) -> FormattedText:
        return FormattedText(
            [
                (
                    "class:placeholder",
                    _PLACEHOLDER_BUSY if self._busy else _PLACEHOLDER_IDLE,
                )
            ]
        )

    def _formatted_prompt(self) -> FormattedText:
        frame = self._busy_frame(time.monotonic())
        status = (
            self._active_status()
            if self._busy
            and self._progress != "off"
            and self._active_status is not None
            else ""
        )
        prompt = list(to_formatted_text(ANSI(status))) if status else []
        if frame:
            prompt.append(("class:busy-indicator", f" {frame}"))
        if status or frame:
            prompt.append(("", "\n\n"))
        prompt_style = (
            f"fg:{active_theme_color(StyleToken.PROMPT)}" if self._color else ""
        )
        prompt.append((prompt_style, self._prompt_text()))
        return FormattedText(prompt)

    def _busy_frame(self, now: float) -> str:
        if not self._busy or self._progress == "off":
            return ""
        if self._progress == "minimal":
            return "•"
        elapsed_ms = (now - self._busy_started_at) * 1_000
        frame_count = len(self._animation_frames)
        frame_index = int(elapsed_ms / self._animation_interval_ms) % frame_count
        return self._animation_frames[frame_index]

    def _prompt_refresh_interval(self) -> float | None:
        if self._busy and self._progress == "full":
            return self._animation_interval_ms / 1_000
        return None

    def _prompt_text(self) -> str:
        if self._disabled:
            return _PROMPT_DISABLED
        if self._busy:
            return _PROMPT_BUSY
        if self._is_resumed:
            return _PROMPT_RESUMED
        return _PROMPT_FRESH
