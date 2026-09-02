import asyncio
import time
from pathlib import Path
from threading import Event
from typing import Any, AsyncIterator, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import QueryError
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.worker import Worker, WorkerCancelled

from openminion.cli.status import PhaseStatusController
from openminion.cli.status.tool_calls import format_public_tool_activity
from openminion.cli.ux.verbosity import VerbosityLevel
from openminion.cli.presentation import (
    ThinkingIndicator,
    build_tool_event_from_progress,
    tool_call_body,
)
from openminion.cli.presentation.animation import (
    AnimationResolution,
    default_animation_registry,
)
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.modules.telemetry.trace.phase_timing import mark_active_chat_first_text

from .files import build_file_index
from .search import ChatSearchBar
from .tokens import cursor_offset_for_text_area
from .input import InputStateMixin
from .turn_queue import FocusTurnQueueMixin
from .shell import FocusShellMixin
from .status import FocusLabelsMixin, FocusRuntimeStateMixin
from .overlay import FocusOverlayInteractionMixin
from .commands import SlashCommandMixin
from .actions import FocusActionMixin
from .runtime.commands import RuntimeCommandMixin
from .runtime.messages import room_result_chat_messages
from .widgets.debug_pane import FocusDebugPane
from .widgets.inline_choice import _InlineChoiceWidget
from .widgets import (
    FileMentionOverlay,
    FocusComposer,
    FocusStatusLine,
    FocusTranscript,
    SlashCommandOverlay,
    ToolApprovalWidget,
)


def _format_response_time(elapsed_seconds: float) -> str:
    elapsed = max(0.0, float(elapsed_seconds))
    if 0.0 < elapsed < 1.0:
        return "<1s"
    seconds = int(elapsed)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m{seconds:02d}s"


class FocusScreen(
    SlashCommandMixin,
    RuntimeCommandMixin,
    FocusActionMixin,
    FocusShellMixin,
    FocusOverlayInteractionMixin,
    InputStateMixin,
    FocusTurnQueueMixin,
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
        Binding("ctrl+enter", "cancel_and_run_next", "Cancel + next"),
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
        progress: str = "full",
        animation: AnimationResolution | None = None,
    ) -> None:
        super().__init__()
        self._runtime = runtime
        self._working_dir = str(Path(working_dir).expanduser().resolve(strict=False))
        self._requested_agent = str(requested_agent or "").strip() or None
        self._requested_session = str(requested_session or "").strip() or None
        self._verbosity: VerbosityLevel = cast(
            VerbosityLevel,
            verbosity if verbosity in ("quiet", "normal", "verbose") else "normal",
        )
        self._progress: str = (
            progress if progress in ("full", "minimal", "off") else "full"
        )
        self._animation_resolution = animation or default_animation_registry().resolve(
            "openminion",
            "braille",
            source="default",
        )
        self._tool_widgets: dict[str, object] = {}
        self._active_turn: Any | None = None
        self._approval_future: asyncio.Future[str] | None = None
        self._approval_widget: ToolApprovalWidget | None = None
        self._prompt_future: asyncio.Future[str] | None = None
        self._prompt_widget: _InlineChoiceWidget | None = None
        self._prompt_kind: str | None = None
        self._session_grants: set[str] = set()
        self._last_turn_debug: dict[str, Any] = {}
        self._session_initializing = True
        self._turn_worker: Worker[None] | None = None
        self._room_cancel_event: Event | None = None
        self._interrupt_requested = False
        self._run_next_after_interrupt = False
        self._cancel_run_next_expected_queue_id: str | None = None
        self._turn_input_queue = self._resolve_turn_input_queue()
        self._suppress_slash_overlay_once = False
        self._suppress_file_overlay_once = False
        self._status_controller = PhaseStatusController(fallback_label="Working...")

    def compose(self) -> ComposeResult:
        with Vertical(id="focus-root"):
            with Vertical(id="focus-screen-main"):
                yield ChatSearchBar(id="focus-search-bar")
                yield FocusTranscript(
                    verbosity=self._verbosity,
                    animation=self._animation_resolution.spec,
                    progress=self._progress,
                )
                yield ThinkingIndicator(
                    animation=self._animation_resolution.spec,
                    progress=self._progress,
                    id="focus-thinking",
                )
                yield FocusDebugPane()
                yield SlashCommandOverlay()
                yield FileMentionOverlay()
                yield FocusComposer()
            yield FocusStatusLine()

    def on_mount(self) -> None:
        self._refresh_header(status_mode="initializing")
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
                        body=str(exc),
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
        active = getattr(self.app, "active_theme", None)
        name = getattr(active, "name", "") if active is not None else ""
        if name:
            return str(name).strip().lower()
        try:
            from openminion.cli.presentation.styles import get_active_theme_name

            return get_active_theme_name()
        except (QueryError, AttributeError):
            return "dark"

    def _animation_label(self) -> str:
        spec = self._animation_resolution.spec
        return f"{spec.provider_id}:{spec.name}"

    def _apply_animation_resolution(self, resolution: AnimationResolution) -> None:
        self._animation_resolution = resolution
        try:
            indicator = self.query_one(ThinkingIndicator)
            indicator.update_animation(resolution.spec, progress=self._progress)
        except (QueryError, AttributeError):
            pass
        try:
            transcript = self.query_one(FocusTranscript)
            transcript.set_animation(resolution.spec, progress=self._progress)
        except (QueryError, AttributeError):
            pass

    def on_focus_composer_submitted(self, event: FocusComposer.Submitted) -> None:
        text = str(event.text or "").strip()
        if self._consume_visible_file_overlay_submission():
            return
        if self._consume_visible_slash_overlay_submission(text):
            return
        if not text:
            return
        if self._session_initializing or not bool(
            getattr(self._runtime, "is_bound", False)
        ):
            return
        if self._submit_active_approval_text(text):
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        if text.startswith("!"):
            if self._busy:
                chat = self.query_one(FocusTranscript)
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body="Shell escape is unavailable while a turn is running.",
                    )
                )
                return
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

    @staticmethod
    def _approval_decision_from_text(text: str) -> str | None:
        """Map typed approval replies to the inline approval decision contract."""
        normalized = " ".join(str(text or "").strip().lower().split())
        if not normalized:
            return None
        if normalized in {"yes", "y", "allow", "approve", "a", "once", "allow once"}:
            return "approve"
        if normalized in {
            "session",
            "s",
            "always",
            "all",
            "allow all",
            "allow session",
            "session allow",
        }:
            return "allow_all"
        if normalized in {"no", "n", "deny", "d", "cancel"}:
            return "deny"
        return None

    def _submit_active_approval_text(self, text: str) -> bool:
        """Resolve active approval prompts before busy-state typeahead queueing."""
        approval_future = self._approval_future
        if approval_future is None or approval_future.done():
            return False
        decision = self._approval_decision_from_text(text)
        if decision is None:
            return False
        approval_future.set_result(decision)
        return True

    def on_input_changed(self, event) -> None:  # type: ignore[no-untyped-def]
        input_widget = getattr(event, "input", None)
        if input_widget is None or getattr(input_widget, "id", "") != "focus-input":
            return
        value = str(getattr(event, "value", "") or "")
        cursor = int(getattr(input_widget, "cursor_position", len(value)))
        self._apply_overlays_for_value(value=value, cursor_offset=cursor)
        self._push_input_state("typing" if value.strip() else "empty")

    def on_text_area_changed(self, event) -> None:  # type: ignore[no-untyped-def]
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
        if self._busy:
            return
        try:
            status_line = self.query_one(FocusStatusLine)
        except (QueryError, AttributeError):
            return
        status_line.set_state(input_state=input_state)

    async def _room_turn_reply(
        self,
        text: str,
        *,
        chat: FocusTranscript,
        turn: Any,
        cancel_event: Event,
    ) -> str:
        result = await self._runtime.run_room_turn(
            text,
            progress_callback=self._handle_progress_event,
            inbound_metadata={
                "workspace_root": self._working_dir,
                "cwd": self._working_dir,
            },
            approval_callback=self._approval_callback,
            cancel_event=cancel_event,
        )
        messages = room_result_chat_messages(result)
        if not messages:
            turn.complete(final_text="")
            return ""
        first, *remaining = messages
        turn._widget._message.sender = first.sender
        turn._widget._message.msg_id = first.msg_id
        turn.complete(final_text=first.body)
        for message in remaining:
            chat.push_message(message)
        return str(first.body)

    async def _stream_turn_tokens(self, text: str) -> AsyncIterator[str]:
        async for chunk in self._runtime.send_message(
            text,
            progress_callback=self._handle_progress_event,
            inbound_metadata={
                "workspace_root": self._working_dir,
                "cwd": self._working_dir,
            },
            approval_callback=self._approval_callback,
        ):
            token = str(chunk or "")
            if token:
                yield token

    async def _run_turn(
        self,
        text: str,
        *,
        render_user: bool = True,
        queue_id: str | None = None,
    ) -> None:
        if self._busy:
            return
        self._set_busy(True)
        self._interrupt_requested = False
        self._status_controller.start_turn()
        self._tool_widgets.clear()
        chat = self.query_one(FocusTranscript)
        if render_user:
            chat.push_message(
                ChatMessage(kind=MessageKind.USER, sender="you", body=text)
            )
        started = time.perf_counter()
        reply = ""
        failed = False
        turn = chat.begin_turn(role="assistant")
        self._active_turn = turn
        turn._widget._message.sender = self._runtime.agent_id
        self._room_cancel_event = Event() if self._runtime.is_room_session() else None
        room_cancel_event = self._room_cancel_event
        try:
            if room_cancel_event is not None:
                reply = await self._room_turn_reply(
                    text,
                    chat=chat,
                    turn=turn,
                    cancel_event=room_cancel_event,
                )
            else:
                async for token in self._stream_turn_tokens(text):
                    reply += token
                    mark_active_chat_first_text()
                    turn.append_token(token)
                turn.complete(final_text=reply)
            if not reply.strip():
                self._drop_empty_streaming_turn(chat, turn)
        except asyncio.CancelledError:
            if room_cancel_event is not None:
                room_cancel_event.set()
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
            failed = True
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=str(exc),
                )
            )
        finally:
            if self._room_cancel_event is room_cancel_event:
                self._room_cancel_event = None
            self._active_turn = None
            interrupted = bool(self._interrupt_requested)
            elapsed_seconds = time.perf_counter() - started
            self._last_turn_debug = {
                "elapsed_ms": int(elapsed_seconds * 1000),
                "session_id": self._runtime.session_id,
                "agent_id": self._runtime.agent_id,
                "working_dir": self._working_dir,
                "reply": reply,
                "interrupted": interrupted,
            }
            self._mark_queue_entry_terminal(
                queue_id,
                interrupted=interrupted,
                failed=failed,
            )
            self._set_busy(False)
            self._status_controller.end_turn()
            self._update_debug_snapshot()
            if not interrupted:
                self._on_turn_complete(elapsed_seconds)
            run_next = (not interrupted) or self._run_next_after_interrupt
            expected_queue_id = self._cancel_run_next_expected_queue_id
            self._interrupt_requested = False
            self._run_next_after_interrupt = False
            self._cancel_run_next_expected_queue_id = None
            self._turn_worker = None
            if run_next:
                self._start_next_queued_turn(expected_queue_id=expected_queue_id)

    def _drop_empty_streaming_turn(self, chat: FocusTranscript, turn) -> None:
        widget = getattr(turn, "_widget", None)
        if widget is None:
            return
        message_id = getattr(getattr(widget, "_message", None), "msg_id", "")
        if message_id:
            chat.drop_message(message_id)

    def _on_turn_complete(self, elapsed_seconds: float) -> None:
        """Record completion timing and write the optional long-completion bell."""
        from openminion.base.config.env import resolve_environment_config
        from openminion.cli.constants import (
            CLI_TRUTHY_ENV_VALUES,
            OPENMINION_FOCUS_BELL_ENV,
            OPENMINION_SHOW_PHASE_TIMING_ENV,
        )
        import sys

        try:
            elapsed_float = float(elapsed_seconds or 0.0)
        except (TypeError, ValueError):
            return
        env = resolve_environment_config()
        try:
            chat = self.query_one(FocusTranscript)
        except (QueryError, AttributeError):
            chat = None
        if env.openminion_show_response_time and chat is not None:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"Done in {_format_response_time(elapsed_float)}",
                )
            )
        if env.get_bool(OPENMINION_SHOW_PHASE_TIMING_ENV, False) and chat is not None:
            from openminion.cli.presentation.timing_report import (
                format_chat_phase_timing_report,
            )

            payload_getter = getattr(
                self._runtime, "last_chat_phase_timing_payload", None
            )
            payload = payload_getter() if callable(payload_getter) else None
            report = format_chat_phase_timing_report(payload)
            if report:
                chat.push_message(
                    ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=report)
                )
        if elapsed_float <= 10.0:
            return
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
        try:
            indicator = self.query_one(ThinkingIndicator)
        except (QueryError, AttributeError):
            indicator = None
        if indicator is not None:
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
        if self._room_cancel_event is not None:
            self._room_cancel_event.set()
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
            view = self._status_controller.update(
                payload,
                verbosity=self._verbosity,
            )
        except (QueryError, AttributeError):
            view = None
        try:
            indicator = self.query_one(ThinkingIndicator)
        except (QueryError, AttributeError):
            return
        if view is None:
            return
        try:
            refreshed = self._status_controller.refresh_view_with_live_elapsed(view)
        except AttributeError:
            refreshed = view
        indicator.view_model = refreshed

    def _push_durable_activity_row(self, payload: dict[str, Any]) -> bool:
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
            status_tool_name = (
                tool_name
                if self._verbosity == "verbose"
                else format_public_tool_activity(
                    tool_event.model_tool_name or tool_name,
                    pending=True,
                )
            )
            self._push_status_line(state="tool", tool_name=status_tool_name)
            active_turn = self._active_turn
            if active_turn is not None:
                widget = active_turn.upsert_tool_block(
                    call_id=call_id,
                    event=tool_event,
                    pending=True,
                    animation=self._animation_resolution.spec,
                    progress=self._progress,
                )
                chat.call_after_refresh(lambda: chat.scroll_end(animate=False))
            else:
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
            active_turn = self._active_turn
            if active_turn is not None:
                widget = active_turn.upsert_tool_block(
                    call_id=call_id,
                    event=tool_event,
                    pending=False,
                    animation=self._animation_resolution.spec,
                    progress=self._progress,
                )
                chat.call_after_refresh(lambda: chat.scroll_end(animate=False))
            else:
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
            if hasattr(widget, "set_tool_result"):
                widget._message.tool_event = tool_event
                widget.set_tool_result(tool_event.content or "Completed.")
            elif hasattr(widget, "update_event"):
                widget.update_event(tool_event, pending=False)
                chat.call_after_refresh(lambda: chat.scroll_end(animate=False))
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
        if self._approval_future is not None and not self._approval_future.done():
            if event.scope == ToolApprovalWidget.SCOPE_SESSION:
                self._approval_future.set_result("allow_all")
            else:
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
