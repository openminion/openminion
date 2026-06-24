# mypy: disable-error-code="attr-defined,no-untyped-def,no-untyped-call,type-arg,var-annotated"

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from types import SimpleNamespace

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import QueryError
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, OptionList

from openminion.cli.tui.presentation import resolve_theme_data_root

from ...widgets import ChatMessage, ChatView, MessageKind, SidebarItem

_TRUST_CATEGORIES = ("exec", "file", "browser", "web", "weather", "ip")


class TrustCategoryModal(ModalScreen[list[str] | None]):
    """Native picker for /trust categories."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, categories: list[str]) -> None:
        super().__init__()
        self._categories = categories
        self._selected: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="trust-modal"):
            yield Label("Grant Session Trust", classes="modal-title")
            yield Label(
                "Choose one or more categories to trust for this session.",
                classes="dim-hint",
            )
            for category in self._categories:
                yield Button(
                    self._label_for(category),
                    id=f"trust-cat-{category}",
                    classes="trust-category-btn",
                )
            with Horizontal(id="trust-modal-buttons"):
                yield Button("Grant trust", id="trust-confirm", variant="primary")
                yield Button("Cancel", id="trust-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("trust-cat-"):
            category = button_id.removeprefix("trust-cat-")
            if category in self._selected:
                self._selected.remove(category)
            else:
                self._selected.add(category)
            event.button.label = self._label_for(category)
            event.stop()
            return
        if button_id == "trust-confirm":
            self.dismiss(sorted(self._selected))
            return
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _label_for(self, category: str) -> str:
        return f"[{'x' if category in self._selected else ' '}] {category}"


class ArtifactsModal(ModalScreen[None]):
    """Native modal for last-turn artifacts."""

    BINDINGS = [("escape", "close_modal", "Close")]

    def __init__(self, artifacts: list[dict]) -> None:
        super().__init__()
        self._artifacts = artifacts

    def compose(self) -> ComposeResult:
        with Vertical(id="artifacts-modal"):
            yield Label("Last Turn Artifacts", classes="modal-title")
            if not self._artifacts:
                yield Label("No artifacts from last turn.", classes="dim-hint")
                return
            table = DataTable(id="artifacts-table")
            table.add_columns("Name", "Type", "Size")
            for artifact in self._artifacts:
                name = str(artifact.get("name", "") or artifact.get("path", "artifact"))
                kind = str(artifact.get("type", "") or "unknown")
                size = str(artifact.get("size", "") or "—")
                table.add_row(name, kind, size)
            yield table

    def action_close_modal(self) -> None:
        self.dismiss(None)


class AgentSwitchModal(ModalScreen[str | None]):
    """Modal chooser for switching the active runtime agent."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "confirm", "Confirm"),
    ]

    def __init__(self, agents: list[SidebarItem], active_agent_id: str) -> None:
        super().__init__()
        self._agents = agents
        self._active_agent_id = active_agent_id
        self._agent_ids = [agent.id for agent in agents]

    def compose(self) -> ComposeResult:
        with Vertical(id="agent-switch-overlay"):
            with Vertical(id="agent-switch-dialog"):
                yield Label("Switch Agent", id="agent-switch-title")
                yield OptionList(
                    *[f"{agent.label} ({agent.id})" for agent in self._agents],
                    id="agent-switch-list",
                )
                yield Label(
                    "↑↓ move  Enter confirm  Esc cancel", id="agent-switch-hint"
                )

    def on_mount(self) -> None:
        option_list = self.query_one("#agent-switch-list", OptionList)
        option_list.focus()
        active_index = next(
            (
                idx
                for idx, agent_id in enumerate(self._agent_ids)
                if agent_id == self._active_agent_id
            ),
            0,
        )
        option_list.highlighted = active_index

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        del event
        self.action_confirm()

    def action_confirm(self) -> None:
        self.dismiss(self._selected_agent_id())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _selected_agent_id(self) -> str | None:
        option_list = self.query_one("#agent-switch-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        if highlighted < 0 or highlighted >= len(self._agent_ids):
            return None
        return self._agent_ids[highlighted]


class ChatCommandMixin:
    def _agents_command_body(self) -> str:
        agents = list(self._runtime.list_agents())
        body = "\n".join(
            f"{'▶' if agent.active else ' '} {agent.id}" for agent in agents
        )
        return f"Agents:\n{body or '(none)'}\n\nUse /agent <id> to switch."

    def _handle_command(self, text: str) -> None:
        normalized = str(text or "").strip()
        if normalized == "/trust":
            self._open_trust_modal()
            return
        if normalized == "/artifacts":
            self._open_artifacts_modal()
            return
        if self._handle_cli_bridge_command(text):
            return

        parts = text.split()
        cmd = parts[0].lower()
        chat = self.query_one(ChatView)

        if cmd == "/agent":
            sub = parts[1] if len(parts) >= 2 else ""
            if sub and sub not in ("inspect", "list"):
                self._do_switch_agent(sub)
                self._refresh_sidebar()
            else:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body=self._agents_command_body(),
                    )
                )
            return

        if cmd == "/session":
            sub = parts[1] if len(parts) >= 2 else ""
            if sub:
                self._do_switch_session(sub)
            else:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body=(
                            "Usage: /session <id>\n"
                            "See the sidebar (Ctrl+B) or ^4 Sessions tab for IDs."
                        ),
                    )
                )
            return

        dispatch: dict[str, str] = {
            "/exit": "quit",
            "/quit": "quit",
            "/new": "new",
            "/clear": "clear",
            "/help": "help",
            "/?": "help",
            "/": "help",
            "/menu": "menu",
            "/theme": "theme",
            "/tools": "tools",
            "/mcp": "mcp",
            "/permissions": "permissions",
            "/diff": "diff",
            "/context": "context",
            "/effort": "effort",
            "/memory": "memory",
            "/skills": "skills",
            "/statusline": "statusline",
            "/undo": "undo",
            "/status": "status",
            "/debug": "debug",
            "/sidebar": "sidebar",
            "/agents": "agents",
        }

        action = dispatch.get(cmd)

        if action == "quit":
            self.app.exit()
        elif action == "new":
            self.action_new_session()
        elif action == "clear":
            chat.clear_messages()
        elif action == "help":
            self.screen.action_show_help()
        elif action == "menu":
            bridge_context = self._build_cli_bridge_context()
            config = (
                bridge_context.config
                if bridge_context is not None
                else SimpleNamespace(
                    runtime=SimpleNamespace(menu_pairing_enabled=False)
                )
            )
            body = self._capture_cli_chat_ui_text(
                self._print_grouped_menu,
                config=config,
            )
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=body or "No menu entries available.",
                )
            )
        elif action == "theme":
            body = self._capture_cli_chat_ui_text(self._handle_theme, normalized)
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=body or "Theme information unavailable.",
                )
            )
        elif action == "sidebar":
            self.action_toggle_sidebar()
        elif action == "tools":
            tools = self._runtime.list_tools()
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="\n".join(f"{'✓' if en else '✗'}  {n}" for n, en in tools)
                    or "(none)",
                )
            )
        elif action == "mcp":
            reporter = getattr(self._runtime, "mcp_status_report", None)
            if callable(reporter):
                try:
                    body = str(reporter() or "").strip() or "No MCP data available."
                except Exception as exc:
                    body = f"MCP status failed: {exc}"
            else:
                body = "This runtime does not expose MCP status."
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=body,
                )
            )
        elif action == "permissions":
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=self._permissions_command_body(normalized),
                )
            )
        elif action == "diff":
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=self._diff_command_body(normalized),
                )
            )
        elif action in {"context", "effort", "memory", "skills", "statusline", "undo"}:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=self._visible_parity_command_body(action, normalized),
                )
            )
        elif action == "agents":
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=self._agents_command_body(),
                )
            )
        elif action == "status":
            rt = self._runtime
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=(
                        f"agent      {rt.agent_id}\n"
                        f"session    {rt.session_id}\n"
                        f"transport  {rt.transport}"
                    ),
                )
            )
        elif action == "debug":
            rt = self._runtime
            channel = getattr(rt, "_channel", "?")
            target = getattr(rt, "_target", "?")
            tool_count = len(self._runtime.list_tools())
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=(
                        f"agent      {rt.agent_id}\n"
                        f"session    {rt.session_id}\n"
                        f"channel    {channel}\n"
                        f"target     {target}\n"
                        f"transport  {rt.transport}\n"
                        f"tools      {tool_count}"
                    ),
                )
            )
        else:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"Unknown command: {cmd}  (try /help or F1)",
                )
            )

    def action_cycle_permission_mode(self) -> None:
        mode = self._cycle_permission_mode()
        try:
            self.query_one(ChatView).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"permissions → {mode}",
                )
            )
        except (QueryError, AttributeError):
            pass

    def _cycle_permission_mode(self) -> str:
        cycler = getattr(self._runtime, "cycle_permission_mode", None)
        if not callable(cycler):
            return "unavailable"
        return str(cycler() or "default")

    def _permissions_command_body(self, text: str) -> str:
        parts = str(text or "").strip().split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if not arg:
            mode = str(
                getattr(self._runtime, "permission_mode", "default") or "default"
            )
            return (
                f"permissions → {mode}\n"
                "Use `/permissions default|readonly|bypass` or Shift+Tab."
            )
        if arg == "cycle":
            return f"permissions → {self._cycle_permission_mode()}"
        setter = getattr(self._runtime, "set_permission_mode", None)
        if not callable(setter):
            return "(/permissions: runtime does not expose set_permission_mode)"
        try:
            return f"permissions → {setter(arg)}"
        except ValueError as exc:
            return f"/permissions: {exc}"

    def _diff_command_body(self, text: str) -> str:
        from openminion.cli.tui.presentation.git.diff import render_git_diff

        parts = str(text or "").strip().split(maxsplit=1)
        args = parts[1].strip() if len(parts) > 1 else ""
        try:
            return render_git_diff(Path.cwd(), args).display_body
        except ValueError as exc:
            return f"/diff: {exc}"

    def _visible_parity_command_body(self, action: str, text: str) -> str:
        from openminion.cli.tui.presentation.visible_parity import (
            handle_effort_command,
            handle_statusline_command,
            handle_undo_command,
            render_context_report,
            render_memory_report,
            render_skills_report,
        )

        parts = str(text or "").strip().split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        if action == "context":
            return render_context_report(self._runtime)
        if action == "effort":
            return handle_effort_command(self._runtime, arg)
        if action == "memory":
            return render_memory_report(self._runtime)
        if action == "skills":
            return render_skills_report(self._runtime)
        if action == "statusline":
            return handle_statusline_command(self._runtime, arg)
        if action == "undo":
            return handle_undo_command(self._runtime, arg, working_dir=str(Path.cwd()))
        return f"Unknown command: {action}"

    def _handle_cli_bridge_command(self, text: str) -> bool:
        normalized = str(text or "").strip()
        if not normalized:
            return False

        bridgeable = (
            "/artifacts",
            "/debug",
            "/pair",
            "/trust",
            "/untrust",
            "/grants",
            "/skill",
            "/identity",
            "/sidecar",
        )
        if not normalized.startswith(bridgeable):
            return False

        chat = self.query_one(ChatView)
        bridge_context = self._build_cli_bridge_context()
        if bridge_context is None:
            if normalized.startswith("/debug"):
                return False
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="This slash command needs a real configured runtime.",
                )
            )
            return True

        from openminion.cli.chat.commands import handle_chat_command

        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                result = handle_chat_command(
                    line=normalized,
                    args=bridge_context.args,
                    config=bridge_context.config,
                    agent_id=self._runtime.agent_id,
                    session_id=self._runtime.session_id,
                    transport=self._runtime.transport,
                    mode=bridge_context.runtime_state.mode,
                    runtime_state=bridge_context.runtime_state,
                    last_artifacts=list(self._last_artifacts),
                    last_turn_debug=dict(self._last_turn_debug),
                )
        except Exception as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=f"Command failed: {type(exc).__name__}: {exc}",
                )
            )
            return True

        output = buffer.getvalue().strip()
        if output:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=output,
                )
            )
        self._apply_cli_bridge_result(result)
        return bool(getattr(result, "handled", True))

    @staticmethod
    def _capture_cli_chat_ui_text(callback, /, *args, **kwargs) -> str:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            callback(*args, **kwargs)
        return buffer.getvalue().strip()

    def _handle_theme(self, line: str = "/theme") -> None:
        from openminion.cli.chat.ui import handle_theme

        handle_theme(
            line=line,
            data_root=resolve_theme_data_root(self._runtime),
            theme_applier=self.app.apply_theme,
            active_theme_name_getter=lambda: self.app.active_theme.name,
        )

    @staticmethod
    def _print_grouped_menu(*, config) -> None:
        from openminion.cli.chat.ui import print_grouped_menu

        print_grouped_menu(config=config)

    def _build_cli_bridge_context(self):
        api_runtime = getattr(self._runtime, "_rt", None)
        config = getattr(api_runtime, "config", None)
        if api_runtime is None or config is None:
            return None

        from openminion.cli.chat.runtime import ChatRuntimeState

        config_path = str(getattr(api_runtime, "config_path", "") or "")
        args = SimpleNamespace(
            config=config_path or None,
            quiet=True,
            no_progress=True,
            verbose=False,
            tools_verbose=False,
        )
        mode = str(getattr(getattr(config, "runtime", None), "process_mode", "") or "")
        runtime_state = ChatRuntimeState(
            endpoint=None,
            transport=self._runtime.transport,
            inproc_runtime=api_runtime,
            mode=mode or "single-process",
            auto_start=False,
            show_progress=False,
            quiet=True,
        )
        return SimpleNamespace(
            args=args,
            config=config,
            runtime_state=runtime_state,
        )

    def _apply_cli_bridge_result(self, result) -> None:
        if bool(getattr(result, "exit", False)):
            self.app.exit()
            return

        next_agent = str(getattr(result, "agent_id", "") or "").strip()
        if next_agent:
            self._do_switch_agent(next_agent)
            self._refresh_sidebar()

        next_session = str(getattr(result, "session_id", "") or "").strip()
        if next_session:
            self._do_switch_session(next_session)

        if bool(getattr(result, "new_conversation", False)):
            self.action_new_session()

    def action_copy_message(self) -> None:
        chat = self.query_one(ChatView)
        notice = "Copied selected message."
        text = chat.copy_selected_message()
        if not text:
            text = chat.copy_last_copyable_message()
            notice = "Copied latest message."
        if not text:
            return
        from . import copy_to_clipboard as copy_to_clipboard_fn

        if copy_to_clipboard_fn(text):
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

    def action_switch_agent(self) -> None:
        agents = list(self._runtime.list_agents())
        active_agent_id = self._runtime.agent_id

        def _on_selected(agent_id: str | None) -> None:
            if not agent_id or agent_id == active_agent_id:
                return
            self._do_switch_agent(agent_id)
            self._refresh_sidebar()

        self.app.push_screen(AgentSwitchModal(agents, active_agent_id), _on_selected)

    def _open_trust_modal(self) -> None:
        def _on_selected(categories: list[str] | None) -> None:
            if not categories:
                return
            for category in categories:
                self._handle_cli_bridge_command(f"/trust {category}")

        self.app.push_screen(TrustCategoryModal(list(_TRUST_CATEGORIES)), _on_selected)

    def _open_artifacts_modal(self) -> None:
        self.app.push_screen(ArtifactsModal(list(self._last_artifacts)))
