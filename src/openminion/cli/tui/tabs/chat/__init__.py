# mypy: disable-error-code="no-untyped-def,type-arg,assignment"

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label

from openminion.cli.status import PhaseStatusController
from openminion.cli.presentation import ThinkingIndicator, copy_to_clipboard  # noqa: F401

from .commands import ChatCommandMixin
from .turns import ChatTurnMixin
from ...widgets import (
    ChatInputBar,
    ChatMessage,
    ChatSearchBar,
    ChatView,
    MessageKind,
    Sidebar,
)
from ...widgets.sidebar import _ClickableItem


class ChatTab(ChatTurnMixin, ChatCommandMixin, Widget):
    class BadgeUpdate(Message):
        def __init__(self, session_id: str, agent_id: str) -> None:
            super().__init__()
            self.session_id = session_id
            self.agent_id = agent_id

    BINDINGS = [
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+n", "new_session", "New session"),
        Binding("ctrl+a", "switch_agent", "Switch agent", priority=True),
        Binding("ctrl+f", "toggle_search", "Search", priority=True),
        # Ctrl+Y stays routed through ChatView.copy_selected_message and
        # ChatView.copy_last_copyable_message in ChatCommandMixin.
        Binding("ctrl+y", "copy_message", "Copy last message"),
        Binding("ctrl+l", "toggle_multiline", "Multiline"),
        Binding("shift+tab", "cycle_permission_mode", "Permissions"),
        Binding("escape", "focus_input", "Focus input"),
    ]

    def __init__(self, runtime, *, sessions_provider=None) -> None:
        super().__init__(id="chat-tab")
        self._runtime = runtime
        self._sessions_provider = sessions_provider
        self._busy = False
        self._last_artifacts: list[dict] = []
        self._last_turn_debug: dict = {}
        self._auto_named_sessions: set[str] = set()
        self._pending_resume_candidate = None
        self._resume_prompt_active = False
        self._active_status_controller: PhaseStatusController | None = None

    def compose(self) -> ComposeResult:
        yield Sidebar()
        with Vertical(id="main"):
            yield ChatSearchBar(id="chat-search-bar")
            yield ChatView()
            yield ThinkingIndicator(id="thinking")
            yield Label("", id="dashboard-token-usage", classes="dim-hint")
            yield ChatInputBar()

    def on_mount(self) -> None:
        self._resolve_pending_session()
        self._refresh_sidebar()
        self.set_interval(0.5, self._tick_elapsed)
        if self._resume_prompt_active:
            self.query_one(ChatInputBar).focus_input()
            return
        try:
            history = self._runtime.get_current_history()
            if history:
                self.query_one(ChatView).set_messages(history)
            else:
                self.query_one(ChatView).push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body="New session · type a message to start · / for commands",
                    )
                )
        except Exception as exc:
            self.query_one(ChatView).push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=f"Could not load history: {exc}",
                )
            )
        self._refresh_token_usage_summary()
        self.query_one(ChatInputBar).focus_input()

    def _resolve_pending_session(self) -> None:
        rt = self._runtime
        if not getattr(rt, "prompt_on_resume", False):
            return
        candidate = (
            rt.consume_pending_candidate_session()
            if hasattr(rt, "consume_pending_candidate_session")
            else None
        )
        if candidate is None:
            self._ensure_session_exists()
            return
        candidate_id = str(getattr(candidate, "id", "") or "")
        if not candidate_id:
            self._ensure_session_exists()
            return
        self._pending_resume_candidate = candidate
        self._resume_prompt_active = True
        short_id = candidate_id[:12]
        age = _format_session_age(
            str(getattr(candidate, "updated_at", "") or "")
            or str(getattr(candidate, "last_activity_at", "") or "")
        )
        prompt = (
            f"Resume session {short_id}"
            + (f" ({age})" if age else "")
            + "? Type `y` to resume, `n` to start fresh "
            + "(any other message starts fresh + sends it)."
        )
        self.query_one(ChatView).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=prompt,
            )
        )

    def _handle_resume_prompt_reply(self, text: str) -> bool:
        if not self._resume_prompt_active:
            return False
        candidate = self._pending_resume_candidate
        self._resume_prompt_active = False
        self._pending_resume_candidate = None
        lowered = (text or "").strip().lower()
        rt = self._runtime
        chat = self.query_one(ChatView)
        if lowered in {"y", "yes"} and candidate is not None:
            candidate_id = str(getattr(candidate, "id", "") or "")
            try:
                rt.bind_session(candidate_id)
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body=f"Resumed session {candidate_id[:12]}.",
                    )
                )
                history = rt.get_current_history()
                if history:
                    chat.set_messages(history)
            except Exception as exc:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.ERROR,
                        sender="error",
                        body=f"Resume failed ({exc}); starting fresh.",
                    )
                )
                try:
                    rt.create_new_session()
                except Exception:
                    pass
            self._refresh_sidebar()
            self._refresh_token_usage_summary()
            return True
        if lowered in {"n", "no"}:
            self._start_fresh_session()
            return True
        self._start_fresh_session()
        return False

    def _ensure_session_exists(self) -> None:
        try:
            self._runtime.create_new_session()
        except Exception:
            pass

    def _start_fresh_session(self) -> None:
        chat = self.query_one(ChatView)
        try:
            self._runtime.create_new_session()
        except Exception as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=f"Fresh-session create failed: {exc}",
                )
            )
        else:
            chat.set_messages([])
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="Started a fresh session.",
                )
            )
        self._refresh_sidebar()
        self._refresh_token_usage_summary()

    def on_show(self) -> None:
        self.query_one(ChatInputBar).focus_input()

    def _refresh_sidebar(self) -> None:
        rt = self._runtime
        sb = self.query_one(Sidebar)
        try:
            sessions = rt.list_sessions(scope="current_agent")
        except TypeError:
            sessions = rt.list_sessions()
        sb.update_sessions(sessions)
        sb.update_agents(rt.list_agents())
        sb.update_tools(rt.list_tools())

    def action_toggle_sidebar(self) -> None:
        self.query_one(Sidebar).toggle()

    def on__clickable_item_selected(self, event: _ClickableItem.Selected) -> None:
        if event.category == "session":
            self._do_switch_session(event.item_id)
        elif event.category == "agent":
            self._do_switch_agent(event.item_id)

    def _do_switch_session(self, session_id: str) -> None:
        try:
            rt = self._runtime
            history = rt.switch_session(session_id)
            self.query_one(ChatView).set_messages(history)
            self._refresh_token_usage_summary()
            self.query_one(Sidebar).set_active_session(session_id)
            self.post_message(self.BadgeUpdate(session_id, rt.agent_id))
        except Exception as exc:
            self.query_one(ChatView).push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=f"Session switch failed: {exc}",
                )
            )

    def _do_switch_agent(self, agent_id: str) -> None:
        try:
            rt = self._runtime
            rt.switch_agent(agent_id)
            if getattr(rt, "prompt_on_resume", False):
                self._resolve_pending_session()
                self._refresh_sidebar()
            self._refresh_token_usage_summary()
            self.query_one(Sidebar).set_active_agent(agent_id)
            self.post_message(self.BadgeUpdate(rt.session_id, agent_id))
        except Exception as exc:
            self.query_one(ChatView).push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=f"Agent switch failed: {exc}",
                )
            )

    def on_chat_input_bar_submitted(self, event: ChatInputBar.Submitted) -> None:
        text = event.text
        if self._resume_prompt_active:
            if self._handle_resume_prompt_reply(text):
                return
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._send_message(text)

    def _send_message(self, text: str) -> None:
        if self._busy:
            self.query_one(ChatView).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="Still working on the previous message…",
                )
            )
            return
        self.query_one(ChatView).push_message(
            ChatMessage(
                kind=MessageKind.USER,
                sender="you",
                body=text,
            )
        )
        self._set_busy(True)
        self.run_worker(self._do_turn(text), exclusive=True)

    def action_focus_input(self) -> None:
        self.query_one(ChatInputBar).focus_input()

    def action_new_session(self) -> None:
        rt = self._runtime
        new_id = rt.new_session()
        self._do_switch_session(new_id)
        self._refresh_sidebar()
        self.query_one(ChatView).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"New session  {new_id}",
            )
        )
        self._auto_named_sessions.discard(new_id)

    def action_toggle_multiline(self) -> None:
        self.query_one(ChatInputBar).toggle_multiline()

    def action_toggle_search(self) -> None:
        search_bar = self.query_one(ChatSearchBar)
        if search_bar.display:
            search_bar.hide()
            self.query_one(ChatInputBar).focus_input()
        else:
            search_bar.show()

    def on_chat_search_bar_search_changed(
        self, event: ChatSearchBar.SearchChanged
    ) -> None:
        self.query_one(ChatView).filter_messages(event.query)

    def on_chat_search_bar_search_closed(
        self, event: ChatSearchBar.SearchClosed
    ) -> None:
        del event
        self.query_one(ChatInputBar).focus_input()

    def _maybe_auto_name_session(self, body: str) -> None:
        session_id = str(getattr(self._runtime, "session_id", "") or "").strip()
        if not session_id or session_id in self._auto_named_sessions:
            return
        provider = self._sessions_provider
        if provider is None:
            return
        sessions = []
        list_all = getattr(provider, "list_all_sessions", None)
        if callable(list_all):
            try:
                sessions = list_all()
            except Exception:
                sessions = []
        current = next(
            (s for s in sessions if str(s.get("id", "")) == session_id), None
        )
        if current is not None and str(current.get("name", "") or "").strip():
            self._auto_named_sessions.add(session_id)
            return
        update = getattr(provider, "update_session_name", None)
        if not callable(update):
            return
        first_line = str(body or "").strip().splitlines()[0][:40].strip()
        if not first_line:
            return
        try:
            update(session_id, first_line)
        except Exception:
            return
        self._auto_named_sessions.add(session_id)
        self._refresh_sidebar()


def _format_session_age(updated_at: str) -> str:
    from openminion.cli.presentation.models import format_chat_timestamp

    return format_chat_timestamp(updated_at)
