from __future__ import annotations

from openminion.cli.presentation import copy_to_clipboard
from openminion.cli.presentation.models import ChatMessage, MessageKind

from .palette import CommandPaletteScreen
from .search import ChatSearchBar
from .widgets import (
    FocusComposer,
    FocusTranscript,
    SessionOverlay,
    ToolsOverlay,
)
from .widgets.debug_pane import FocusDebugPane

_PALETTE_ENTRIES = [
    ("/new", "Start a new CLI session", "cmd-new"),
    ("/clear", "Clear the visible transcript", "cmd-clear"),
    ("/tools", "Show available tools", "cmd-tools"),
    ("/sessions", "Show recent sessions", "cmd-sessions"),
    ("/status", "Show CLI runtime status", "cmd-status"),
    ("/debug", "Toggle debug pane", "cmd-debug"),
    ("/exit", "Exit the interactive CLI", "cmd-exit"),
]


class FocusActionMixin:
    """Keyboard and palette actions owned by the interactive screen."""

    def action_command_palette(self) -> None:
        def on_result(result: str | None) -> None:
            actions = {
                "cmd-new": self.action_new_session,
                "cmd-clear": self.action_clear_screen,
                "cmd-tools": self.action_show_tools,
                "cmd-sessions": self.action_show_sessions,
                "cmd-debug": self.action_toggle_debug,
            }
            if result == "cmd-status":
                self._handle_command("/status")
            elif result == "cmd-exit":
                self.run_worker(self._confirm_exit(), exclusive=False)
            elif result in actions:
                actions[result]()

        self.app.push_screen(CommandPaletteScreen(entries=_PALETTE_ENTRIES), on_result)

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
        chat = self.query_one(FocusTranscript)
        text = chat.copy_selected_message()
        notice = "Copied selected message."
        if not text:
            text = chat.copy_last_copyable_message()
            notice = "Copied latest message."
        if not text:
            return
        body = (
            notice
            if copy_to_clipboard(text)
            else "Clipboard not available on this platform."
        )
        chat.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def action_new_session(self) -> None:
        session_id = self._runtime.create_new_session()
        chat = self.query_one(FocusTranscript)
        chat.set_messages([])
        self._tool_widgets.clear()
        self._session_grants.clear()
        self._load_history()
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"New session {session_id}",
            )
        )

    def action_show_tools(self) -> None:
        self.app.push_screen(ToolsOverlay(list(self._runtime.list_tools())))

    def action_show_sessions(self) -> None:
        sessions = list(
            getattr(self._runtime, "list_directory_sessions", lambda **_: [])()
        )

        def on_pick(session_id: str | None) -> None:
            if session_id:
                self._runtime.bind_session(session_id)
                self._load_history()

        self.app.push_screen(SessionOverlay(sessions), on_pick)

    def action_clear_screen(self) -> None:
        self.query_one(FocusTranscript).clear_messages()

    def action_toggle_debug(self) -> None:
        self.query_one(FocusDebugPane).toggle()

    def action_toggle_multiline(self) -> None:
        self.query_one(FocusComposer).toggle_multiline()

    def action_interrupt_turn(self) -> None:
        if self._busy:
            self.run_worker(self._confirm_interrupt(), exclusive=False)

    def action_cancel_and_run_next(self) -> None:
        if self._busy:
            self.run_worker(self._cancel_current_and_run_next(), exclusive=False)

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
        elif self._busy:
            self.run_worker(self._interrupt_current_turn(), exclusive=False)
        else:
            self.run_worker(self._confirm_exit(), exclusive=False)

    async def _confirm_exit(self) -> None:
        if await self._ask_inline("Exit the interactive CLI?", kind="exit"):
            self.app.exit()
