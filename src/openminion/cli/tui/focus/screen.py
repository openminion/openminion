from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import QueryError
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.worker import Worker, WorkerCancelled

from openminion.cli.status import PhaseStatusController
from openminion.cli.tui.presentation import (
    ThinkingIndicator,
    build_tool_event_from_progress,
    copy_to_clipboard,
    format_progress_label,
    tool_call_body,
)
from openminion.cli.tui.screen import CommandPaletteScreen
from openminion.cli.tui.presentation.models import ChatMessage, MessageKind
from openminion.cli.tui.widgets import (
    ChatSearchBar,
)

from .files import build_file_index
from .tokens import cursor_offset_for_text_area
from .input import InputStateMixin
from .status import FocusLabelsMixin, FocusRuntimeStateMixin
from .overlay import FocusOverlayInteractionMixin
from .commands import SlashCommandMixin
from .widgets.debug_pane import FocusDebugPane
from .widgets.inline_choice import _InlineChoiceWidget
from .widgets import (
    FileMentionOverlay,
    FocusComposer,
    FocusStatusLine,
    FocusTranscript,
    SessionOverlay,
    SlashCommandOverlay,
    ToolApprovalWidget,
    ToolsOverlay,
)

_FOCUS_PALETTE_ENTRIES = [
    ("/new", "Start a new focus session", "cmd-new"),
    ("/clear", "Clear the visible transcript", "cmd-clear"),
    ("/tools", "Show available tools", "cmd-tools"),
    ("/sessions", "Show recent sessions", "cmd-sessions"),
    ("/status", "Show focus runtime status", "cmd-status"),
    ("/debug", "Toggle debug pane", "cmd-debug"),
    ("/exit", "Quit focus mode", "cmd-exit"),
]


class FocusScreen(
    SlashCommandMixin,
    FocusOverlayInteractionMixin,
    InputStateMixin,
    FocusLabelsMixin,
    FocusRuntimeStateMixin,
    Screen,
):
    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("ctrl+c", "interrupt_turn", "Interrupt", priority=True),
        Binding("ctrl+f", "toggle_search", "Search", priority=True),
        Binding("ctrl+y", "copy_last_agent", "Copy"),
        Binding("ctrl+n", "new_session", "New session"),
        Binding("ctrl+s", "show_sessions", "Sessions"),
        Binding("ctrl+t", "show_tools", "Tools"),
        Binding("ctrl+d", "toggle_debug", "Debug", priority=True),
        Binding("ctrl+k", "clear_screen", "Clear", priority=True),
        Binding("ctrl+l", "toggle_multiline", "Multiline"),
        Binding("shift+tab", "cycle_permission_mode", "Permissions"),
        Binding("escape", "handle_escape", "Escape"),
    ]

    _busy: reactive[bool] = reactive(False)

    def __init__(
        self,
        *,
        runtime,
        working_dir: str,
        requested_agent: str | None = None,
        requested_session: str | None = None,
        verbosity: str = "normal",
    ) -> None:
        super().__init__()
        self._runtime = runtime
        self._working_dir = str(Path(working_dir).expanduser().resolve(strict=False))
        self._requested_agent = str(requested_agent or "").strip() or None
        self._requested_session = str(requested_session or "").strip() or None
        self._verbosity: str = (
            verbosity if verbosity in ("quiet", "normal", "verbose") else "normal"
        )
        self._tool_widgets: dict[str, object] = {}
        self._approval_future: asyncio.Future[str] | None = None
        self._approval_widget: ToolApprovalWidget | None = None
        self._prompt_future: asyncio.Future[str] | None = None
        self._prompt_widget: _InlineChoiceWidget | None = None
        self._prompt_kind: str | None = None
        self._session_grants: set[str] = set()
        self._last_turn_debug: dict[str, Any] = {}
        self._session_initializing = True
        self._turn_worker: Worker[None] | None = None
        self._interrupt_requested = False
        self._queued_turns: list[str] = []
        self._suppress_slash_overlay_once = False
        self._suppress_file_overlay_once = False
        self._status_controller = PhaseStatusController(fallback_label="Working...")

    def compose(self) -> ComposeResult:
        with Vertical(id="focus-root"):
            with Vertical(id="focus-screen-main"):
                yield ChatSearchBar(id="focus-search-bar")
                yield FocusTranscript(verbosity=self._verbosity)
                yield ThinkingIndicator(id="focus-thinking")
                yield FocusDebugPane()
                yield SlashCommandOverlay()
                yield FileMentionOverlay()
                yield FocusComposer()
            yield FocusStatusLine()

    def on_mount(self) -> None:
        self._refresh_header(status_mode="idle")
        self._sync_input_state()
        self.set_interval(0.5, self._tick_status_line)
        try:
            overlay = self.query_one(SlashCommandOverlay)
            overlay.set_items(
                [
                    (aliases[0], description)
                    for aliases, description, _handler in self._slash_command_registry
                ]
            )
        except (QueryError, AttributeError):
            pass
        try:
            file_overlay = self.query_one(FileMentionOverlay)
            file_overlay.set_items(build_file_index(self._working_dir))
        except (QueryError, AttributeError):
            pass
        self.run_worker(self._initialize_session(), exclusive=True)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action == "interrupt_turn":
            return self._busy
        return super().check_action(action, parameters)

    def _tick_status_line(self) -> None:
        self._push_status_line()
        if not self._busy:
            return
        self._tick_thinking_elapsed()

    def _tick_thinking_elapsed(self) -> None:
        """Refresh the focus ThinkingIndicator elapsed time."""
        try:
            indicator = self.query_one(ThinkingIndicator)
        except (QueryError, AttributeError):
            return
        if not bool(getattr(indicator, "is_thinking", False)):
            return
        snapshot = self._status_controller.snapshot_elapsed_text()
        if snapshot is not None:
            indicator.elapsed_text = snapshot

    async def _initialize_session(self) -> None:
        try:
            if bool(getattr(self._runtime, "is_bound", False)):
                self._load_history()
                return
            candidate = None
            finder = getattr(self._runtime, "find_candidate_session", None)
            if callable(finder):
                candidate = finder()
            if candidate is not None:
                age = self._session_age_label(
                    str(getattr(candidate, "updated_at", "") or "")
                )
                resume = await self._ask_inline(
                    f"Resume last session ({age})?", kind="resume"
                )
                if resume:
                    self._runtime.bind_session(str(getattr(candidate, "id", "") or ""))
                else:
                    self._runtime.create_new_session()
            else:
                self._runtime.create_new_session()
            self._load_history()
        except Exception as exc:
            self.query_one(FocusTranscript).set_messages(
                [
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=self._append_error_hint(str(exc)),
                    )
                ]
            )
            self._update_debug_snapshot()
        finally:
            self._session_initializing = False
            self._sync_input_state()

    def _load_history(self) -> None:
        history = list(self._runtime.get_current_history() or [])
        chat = self.query_one(FocusTranscript)
        is_resumed = bool(history)
        if is_resumed:
            chat.set_messages(history)
        else:
            from .widgets.greeter import build_greeter_message

            chat.set_messages(
                [
                    build_greeter_message(
                        runtime=self._runtime,
                        working_dir=self._working_dir,
                        theme_name=self._active_theme_name(),
                    )
                ]
            )
        try:
            input_bar = self.query_one(FocusComposer)
            input_bar.set_resumed(is_resumed)
        except (QueryError, AttributeError):
            pass
        self._refresh_header(status_mode="idle")
        self._sync_input_state()
        self._update_debug_snapshot()

    def _active_theme_name(self) -> str:
        """Return the active theme name for focus mode."""
        active = getattr(self.app, "active_theme", None)
        name = getattr(active, "name", "") if active is not None else ""
        if name:
            return str(name).strip().lower()
        try:
            from openminion.cli.presentation.styles import get_active_theme_name

            return get_active_theme_name()
        except (QueryError, AttributeError):
            return "dark"

    def on_focus_composer_submitted(self, event: FocusComposer.Submitted) -> None:
        text = str(event.text or "").strip()
        if self._consume_visible_file_overlay_submission():
            return
        if self._consume_visible_slash_overlay_submission():
            return
        if not text:
            return
        if self._session_initializing or not bool(
            getattr(self._runtime, "is_bound", False)
        ):
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        if text.startswith("!"):
            command = text[1:].strip()
            if command:
                self._turn_worker = self.run_worker(
                    self._run_shell_escape(command), exclusive=True
                )
            return
        if self._busy:
            self._queue_turn(text)
            return
        self._start_turn_worker(text)

    def on_input_changed(self, event) -> None:  # type: ignore[no-untyped-def]
        """Update overlays for single-line input changes."""
        input_widget = getattr(event, "input", None)
        if input_widget is None or getattr(input_widget, "id", "") != "focus-input":
            return
        value = str(getattr(event, "value", "") or "")
        cursor = int(getattr(input_widget, "cursor_position", len(value)))
        self._apply_overlays_for_value(value=value, cursor_offset=cursor)
        self._push_input_state("typing" if value.strip() else "empty")

    def on_text_area_changed(self, event) -> None:  # type: ignore[no-untyped-def]
        """Update overlays for multiline editor changes."""
        text_area = getattr(event, "text_area", None)
        if text_area is None or getattr(text_area, "id", "") != "focus-editor":
            return
        value = str(getattr(text_area, "text", "") or "")
        try:
            line, col = text_area.cursor_location
        except (QueryError, AttributeError):
            line, col = 0, len(value.split("\n")[-1])
        cursor = cursor_offset_for_text_area(value, int(line), int(col))
        self._apply_overlays_for_value(value=value, cursor_offset=cursor)
        self._push_input_state("typing" if value.strip() else "empty")

    def _push_input_state(self, input_state: str) -> None:
        """Push the current input-state hint onto FocusStatusLine."""
        if self._busy:
            return
        try:
            status_line = self.query_one(FocusStatusLine)
        except (QueryError, AttributeError):
            return
        status_line.set_state(input_state=input_state)

    async def _run_shell_escape(self, command: str) -> None:
        """Run a ``!cmd`` shell escape via the local subprocess."""
        import shlex
        from openminion.cli.tui.focus.widgets import FocusTranscript
        from openminion.cli.tui.presentation.models import ToolEvent

        if self._busy:
            return
        self._set_busy(True)
        chat = self.query_one(FocusTranscript)
        chat.push_message(
            ChatMessage(
                kind=MessageKind.USER,
                sender="you",
                body=f"!{command}",
            )
        )
        try:
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=f"Could not parse `!{command}`: {exc}",
                    )
                )
                return
            if not argv:
                return
            try:
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    cwd=self._working_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=f"Command not found: {exc.filename or argv[0]}",
                    )
                )
                return
            except Exception as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=f"Could not run `!{command}`: {exc}",
                    )
                )
                return
            stdout_b, stderr_b = await proc.communicate()
            stdout = (stdout_b or b"").decode("utf-8", errors="replace")
            stderr = (stderr_b or b"").decode("utf-8", errors="replace")
            combined = stdout
            if stderr:
                combined = (combined + ("\n" if combined else "") + stderr).rstrip()
            event = ToolEvent(
                tool_name="bash",
                args={"cmd": command},
                content=combined or "(no output)",
                full_content=combined,
                duration_ms=0,
                exit_code=int(proc.returncode or 0),
            )
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.TOOL,
                    sender="bash",
                    body="",
                    tool_event=event,
                    tool_result=combined,
                )
            )
        finally:
            self._set_busy(False)

    def _queue_turn(self, text: str) -> None:
        self._queued_turns.append(text)
        chat = self.query_one(FocusTranscript)
        chat.push_message(ChatMessage(kind=MessageKind.USER, sender="you", body=text))
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"Queued message ({len(self._queued_turns)} pending).",
            )
        )
        self._push_status_line(state="responding")

    def _start_turn_worker(self, text: str, *, render_user: bool = True) -> None:
        self._turn_worker = self.run_worker(
            self._run_turn(text, render_user=render_user), exclusive=True
        )

    def _start_next_queued_turn(self) -> None:
        if self._busy or self._turn_worker is not None or not self._queued_turns:
            return
        text = self._queued_turns.pop(0)
        self._start_turn_worker(text, render_user=False)

    async def _run_turn(self, text: str, *, render_user: bool = True) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._interrupt_requested = False
        self._status_controller.start_turn()
        chat = self.query_one(FocusTranscript)
        if render_user:
            chat.push_message(
                ChatMessage(kind=MessageKind.USER, sender="you", body=text)
            )
        started = time.perf_counter()
        reply = ""
        turn = chat.begin_turn(role="assistant")
        turn._widget._message.sender = self._runtime.agent_id
        try:
            async for chunk in self._runtime.send_message(
                text,
                progress_callback=self._handle_progress_event,
                inbound_metadata={"workspace_root": self._working_dir},
                approval_callback=self._approval_callback,
            ):
                token = str(chunk or "")
                if not token:
                    continue
                reply += token
                turn.append_token(token)
            turn.complete(final_text=reply)
            if not reply.strip():
                self._drop_empty_streaming_turn(chat, turn)
        except asyncio.CancelledError:
            interrupted = self._interrupt_requested
            turn.complete(final_text=reply)
            if interrupted and not reply.strip():
                self._drop_empty_streaming_turn(chat, turn)
            if interrupted:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body="Interrupted current turn.",
                    )
                )
            else:
                raise
        except Exception as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=self._append_error_hint(str(exc)),
                )
            )
        finally:
            elapsed_seconds = time.perf_counter() - started
            self._last_turn_debug = {
                "elapsed_ms": int(elapsed_seconds * 1000),
                "session_id": self._runtime.session_id,
                "agent_id": self._runtime.agent_id,
                "working_dir": self._working_dir,
                "reply": reply,
                "interrupted": bool(self._interrupt_requested),
            }
            self._set_busy(False)
            self._status_controller.end_turn()
            self._update_debug_snapshot()
            if not self._interrupt_requested:
                self._on_turn_complete(elapsed_seconds)
            self._interrupt_requested = False
            self._turn_worker = None
            self._start_next_queued_turn()

    _ERROR_HINT_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            ("api key", "unauthorized", "401"),
            "→ Run `openminion config init` to set credentials.",
        ),
        (
            ("connection", "network", "timeout"),
            "→ Check your network or retry; transient failures are common.",
        ),
        (
            ("permission denied", "eacces"),
            "→ Verify file permissions in the working directory.",
        ),
        (
            ("not found", "enoent"),
            "→ Confirm the path or session id exists.",
        ),
    )

    def _drop_empty_streaming_turn(self, chat: FocusTranscript, turn) -> None:
        """Prune an assistant streaming turn that produced no visible text."""
        widget = getattr(turn, "_widget", None)
        if widget is None:
            return
        message_id = getattr(getattr(widget, "_message", None), "msg_id", "")
        if message_id:
            chat.drop_message(message_id)

    @classmethod
    def _append_error_hint(cls, body: str) -> str:
        """Append a short actionable hint to eligible error text."""
        text = str(body or "")
        if not text.strip():
            return text
        if text.count("\n") + 1 >= 3:
            return text
        haystack = text.lower()
        for patterns, hint in cls._ERROR_HINT_PATTERNS:
            if any(pattern in haystack for pattern in patterns):
                return f"{text}\n{hint}"
        return text

    def _on_turn_complete(self, elapsed_seconds: float) -> None:
        """Write the terminal bell on long completions when enabled."""
        from openminion.base.config.env import resolve_environment_config
        from openminion.cli.constants import (
            CLI_TRUTHY_ENV_VALUES,
            OPENMINION_FOCUS_BELL_ENV,
        )
        import sys

        try:
            elapsed_float = float(elapsed_seconds or 0.0)
        except (TypeError, ValueError):
            return
        if elapsed_float <= 10.0:
            return
        env = resolve_environment_config()
        raw = str(env.get(OPENMINION_FOCUS_BELL_ENV, "") or "").strip().lower()
        if raw not in CLI_TRUTHY_ENV_VALUES:
            return
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except (QueryError, AttributeError):
            pass

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        indicator = self.query_one(ThinkingIndicator)
        indicator.is_thinking = busy
        self._sync_input_state()
        self._refresh_header(status_mode="responding" if busy else "idle")
        self._push_status_line(state="responding" if busy else "idle")

    def _dismiss_turn_owned_interactions(self) -> None:
        """Best-effort cleanup for inline widgets/futures tied to a turn."""
        approval_future = self._approval_future
        approval_widget = self._approval_widget
        self._approval_future = None
        self._approval_widget = None
        if approval_future is not None and not approval_future.done():
            try:
                approval_future.set_result("deny")
            except (QueryError, AttributeError):
                pass
        if approval_widget is not None:
            try:
                approval_widget.remove()
            except (QueryError, AttributeError):
                pass

        if self._prompt_kind not in {None, "interrupt"}:
            prompt_future = self._prompt_future
            prompt_widget = self._prompt_widget
            self._prompt_future = None
            self._prompt_widget = None
            self._prompt_kind = None
            if prompt_future is not None and not prompt_future.done():
                try:
                    prompt_future.set_result("no")
                except (QueryError, AttributeError):
                    pass
            if prompt_widget is not None:
                try:
                    prompt_widget.remove()
                except (QueryError, AttributeError):
                    pass

    async def _interrupt_current_turn(self) -> None:
        worker = self._turn_worker
        if worker is None:
            return
        self._interrupt_requested = True
        self._dismiss_turn_owned_interactions()
        worker.cancel()
        try:
            await worker.wait()
        except WorkerCancelled:
            pass

    async def _confirm_interrupt(self) -> None:
        if not self._busy:
            return
        should_interrupt = await self._ask_inline(
            "Interrupt current turn?", kind="interrupt"
        )
        if should_interrupt:
            await self._interrupt_current_turn()

    def _handle_progress_event(self, payload: dict[str, Any]) -> None:
        kind = str(payload.get("kind", "") or "").strip()
        if kind.startswith("tool_"):
            self._handle_tool_progress(payload)
            return
        if self._push_durable_activity_row(payload):
            return
        try:
            view = self._status_controller.update(payload)
        except (QueryError, AttributeError):
            view = None
        indicator = self.query_one(ThinkingIndicator)
        if view is None:
            label = format_progress_label(payload, fallback_label="Working...")
            indicator.status_label = label
            return
        try:
            refreshed = self._status_controller.refresh_view_with_live_elapsed(view)
        except AttributeError:
            refreshed = view
        indicator.view_model = refreshed

    def _push_durable_activity_row(self, payload: dict[str, Any]) -> bool:
        """Push a durable non-tool activity row when the payload matches."""
        try:
            from openminion.cli.status.activity_ledger import (
                KIND_APPROVAL,
                KIND_BACKGROUND,
                KIND_BUDGET,
                KIND_ERROR,
                KIND_PLAN,
                activity_from_progress_payload,
                format_activity_line,
            )
        except (QueryError, AttributeError):
            return False
        event = activity_from_progress_payload(payload)
        if event is None or event.kind not in {
            KIND_PLAN,
            KIND_APPROVAL,
            KIND_BACKGROUND,
            KIND_BUDGET,
            KIND_ERROR,
        }:
            return False
        line = format_activity_line(event)
        if not line:
            return False
        message_kind = (
            MessageKind.ERROR if event.kind == KIND_ERROR else MessageKind.SYSTEM
        )
        try:
            chat = self.query_one(FocusTranscript)
            chat.push_message(
                ChatMessage(
                    kind=message_kind,
                    sender=f"activity:{event.kind}",
                    body=line,
                )
            )
        except (QueryError, AttributeError):
            return False
        return True

    def _handle_tool_progress(self, payload: dict[str, Any]) -> None:
        kind = str(payload.get("kind", "") or "").strip()
        tool_event = build_tool_event_from_progress(
            payload, normalize_args=self._normalize_tool_args
        )
        tool_name = tool_event.tool_name
        call_id = str(
            payload.get("call_id", "") or f"tool-{len(self._tool_widgets) + 1}"
        )
        chat = self.query_one(FocusTranscript)
        if kind == "tool_started":
            self._refresh_header(status_mode="tool")
            self._push_status_line(state="tool", tool_name=tool_name)
            widget = chat.push_message(
                ChatMessage(
                    kind=MessageKind.TOOL,
                    sender=f"tool:{tool_name}",
                    body=tool_call_body(tool_event),
                    tool_event=tool_event,
                    tool_result=None,
                )
            )
            self._tool_widgets[call_id] = widget
            return

        widget = self._tool_widgets.get(call_id)
        if widget is None:
            widget = chat.push_message(
                ChatMessage(
                    kind=MessageKind.TOOL,
                    sender=f"tool:{tool_name}",
                    body=tool_call_body(tool_event),
                    tool_event=tool_event,
                    tool_result=tool_event.content or "Completed.",
                )
            )
            self._tool_widgets[call_id] = widget
        else:
            widget._message.tool_event = tool_event
            widget.set_tool_result(tool_event.content or "Completed.")
        self._refresh_header(status_mode="responding" if self._busy else "idle")
        self._push_status_line(
            state="responding" if self._busy else "idle",
            tool_name="",
        )

    async def _approval_callback(
        self,
        tool_name: str,
        args: dict[str, Any],
        call_id: Any,
    ) -> bool:
        normalized_tool_name = str(tool_name or "").strip()
        if normalized_tool_name in self._session_grants:
            return True
        if self._approval_future is not None and not self._approval_future.done():
            return False
        loop = asyncio.get_running_loop()
        self._approval_future = loop.create_future()
        self._approval_widget = ToolApprovalWidget(
            normalized_tool_name,
            self._normalize_tool_args(args),
            allow_all=True,
        )
        self._mount_inline(self._approval_widget)
        self._approval_widget.focus()
        self._refresh_header(status_mode="tool")
        decision = await self._approval_future
        widget = self._approval_widget
        self._approval_future = None
        self._approval_widget = None
        if widget is not None:
            widget.remove()
        if decision == "allow_all":
            self._session_grants.add(normalized_tool_name)
            return True
        return decision == "approve"

    async def _ask_inline(self, prompt: str, *, kind: str = "generic") -> bool:
        if self._prompt_future is not None and not self._prompt_future.done():
            return False
        loop = asyncio.get_running_loop()
        self._prompt_future = loop.create_future()
        self._prompt_kind = str(kind or "generic").strip() or "generic"
        self._prompt_widget = _InlineChoiceWidget(prompt)
        self._mount_inline(self._prompt_widget)
        self._prompt_widget.focus()
        result = await self._prompt_future
        widget = self._prompt_widget
        self._prompt_future = None
        self._prompt_widget = None
        self._prompt_kind = None
        if widget is not None:
            widget.remove()
        return result == "yes"

    def _mount_inline(self, widget: Widget) -> None:
        chat = self.query_one(FocusTranscript)
        chat.mount(widget)
        self.call_after_refresh(lambda: chat.scroll_end(animate=False))

    def on_tool_approval_widget_approved(
        self, event: ToolApprovalWidget.Approved
    ) -> None:
        del event
        if self._approval_future is not None and not self._approval_future.done():
            self._approval_future.set_result("approve")

    def on_tool_approval_widget_denied(self, event: ToolApprovalWidget.Denied) -> None:
        del event
        if self._approval_future is not None and not self._approval_future.done():
            self._approval_future.set_result("deny")

    def on_tool_approval_widget_allow_all(
        self, event: ToolApprovalWidget.AllowAll
    ) -> None:
        del event
        if self._approval_future is not None and not self._approval_future.done():
            self._approval_future.set_result("allow_all")

    def on__inline_choice_widget_selected(
        self, event: _InlineChoiceWidget.Selected
    ) -> None:
        if self._prompt_future is not None and not self._prompt_future.done():
            self._prompt_future.set_result(event.choice)

    def action_command_palette(self) -> None:
        def _on_result(result: str | None) -> None:
            if result == "cmd-new":
                self.action_new_session()
            elif result == "cmd-clear":
                self.action_clear_screen()
            elif result == "cmd-tools":
                self.action_show_tools()
            elif result == "cmd-sessions":
                self.action_show_sessions()
            elif result == "cmd-status":
                self._handle_command("/status")
            elif result == "cmd-debug":
                self.action_toggle_debug()
            elif result == "cmd-exit":
                self.run_worker(self._confirm_exit(), exclusive=False)

        self.app.push_screen(
            CommandPaletteScreen(entries=_FOCUS_PALETTE_ENTRIES), _on_result
        )

    def action_toggle_search(self) -> None:
        search = self.query_one(ChatSearchBar)
        if search.display:
            search.hide()
            self.query_one(FocusComposer).focus_input()
        else:
            search.show()

    def on_chat_search_bar_search_changed(
        self, event: ChatSearchBar.SearchChanged
    ) -> None:
        self.query_one(FocusTranscript).filter_messages(event.query)

    def on_chat_search_bar_search_closed(
        self, event: ChatSearchBar.SearchClosed
    ) -> None:
        del event
        self.query_one(FocusComposer).focus_input()

    def action_copy_last_agent(self) -> None:
        """Focus `Ctrl+Y`: copy selected chat row, or latest as fallback."""

        chat = self.query_one(FocusTranscript)
        notice = "Copied selected message."
        text = chat.copy_selected_message()
        if not text:
            text = chat.copy_last_copyable_message()
            notice = "Copied latest message."
        if not text:
            return
        if copy_to_clipboard(text):
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=notice,
                )
            )
        else:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="Clipboard not available on this platform.",
                )
            )

    def action_new_session(self) -> None:
        session_id = self._runtime.create_new_session()
        self.query_one(FocusTranscript).set_messages([])
        self._tool_widgets.clear()
        self._session_grants.clear()
        self._load_history()
        self.query_one(FocusTranscript).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"New focus session {session_id}",
            )
        )

    def action_show_tools(self) -> None:
        self.app.push_screen(ToolsOverlay(list(self._runtime.list_tools())))

    def action_show_sessions(self) -> None:
        sessions = list(
            getattr(self._runtime, "list_directory_sessions", lambda **_: [])()
        )

        def _on_pick(session_id: str | None) -> None:
            if not session_id:
                return
            self._runtime.bind_session(session_id)
            self._load_history()

        self.app.push_screen(SessionOverlay(sessions), _on_pick)

    def action_clear_screen(self) -> None:
        self.query_one(FocusTranscript).clear_messages()

    def action_toggle_debug(self) -> None:
        self.query_one(FocusDebugPane).toggle()

    def action_toggle_multiline(self) -> None:
        self.query_one(FocusComposer).toggle_multiline()

    def action_interrupt_turn(self) -> None:
        if not self._busy:
            return
        self.run_worker(self._confirm_interrupt(), exclusive=False)

    def action_handle_escape(self) -> None:
        file_overlay = self._file_overlay()
        if file_overlay is not None and file_overlay.visible:
            file_overlay.visible = False
            return
        overlay = self._slash_overlay()
        if overlay is not None and overlay.visible:
            overlay.visible = False
            return
        search = self.query_one(ChatSearchBar)
        if search.display:
            search.hide()
            return
        if self._busy:
            self.run_worker(self._confirm_interrupt(), exclusive=False)
            return
        self.run_worker(self._confirm_exit(), exclusive=False)

    async def _confirm_exit(self) -> None:
        should_exit = await self._ask_inline("Exit focus mode?", kind="exit")
        if should_exit:
            self.app.exit()
