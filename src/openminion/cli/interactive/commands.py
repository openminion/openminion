from __future__ import annotations

import contextlib
import io
from typing import Any, cast

from textual.css.query import QueryError

from openminion.cli.tui.project_context import (
    resolve_project_context,
    write_init_template,
)
from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.permissions import (
    apply_permission_menu_choice,
    format_permission_status_label,
)
from openminion.cli.presentation import resolve_theme_data_root
from openminion.cli.presentation.detail_modes import resolve_details_mode
from openminion.cli.presentation.queue import (
    queue_cleared_notice,
    queue_command_usage_notice,
    queue_drop_missing_notice,
    queue_drop_notice,
    queue_drop_usage_notice,
    queue_listing,
)
from openminion.cli.presentation.slash_commands import rich_slash_command_registry
from openminion.services.runtime.turn_input import TurnInputQueueStatus

from .widgets import FocusTranscript, PermissionsOverlay


class SlashCommandMixin:
    @property
    def _slash_command_registry(self) -> list[tuple[tuple[str, ...], str, str]]:
        return rich_slash_command_registry()

    def _handle_command(self, text: str) -> None:
        normalized = str(text or "").strip()
        if not normalized:
            return
        parts = normalized.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        for aliases, _description, handler_name in self._slash_command_registry:
            if cmd in aliases or normalized in aliases:
                handler = getattr(self, handler_name, None)
                if callable(handler):
                    handler(args)
                return
        self.query_one(FocusTranscript).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"Unknown command: {normalized}",
            )
        )

    def _slash_new(self, _args: str) -> None:
        self.action_new_session()

    def _slash_init(self, _args: str) -> None:
        self.run_worker(self._run_init_command(), exclusive=False)

    def _slash_clear(self, _args: str) -> None:
        self.action_clear_screen()

    def _slash_tools(self, _args: str) -> None:
        self.action_show_tools()

    def _slash_mcp(self, _args: str) -> None:
        body_getter = getattr(self._runtime, "mcp_status_report", None)
        if not callable(body_getter):
            body = "This runtime does not expose MCP status."
        else:
            try:
                body = str(body_getter() or "").strip() or "No MCP data available."
            except Exception as exc:
                body = f"MCP status failed: {exc}"
        self.query_one(FocusTranscript).push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def _slash_sessions(self, _args: str) -> None:
        self.action_show_sessions()

    def _slash_theme(self, args: str) -> None:
        from openminion.cli.presentation.theme import handle_theme

        chat = self.query_one(FocusTranscript)
        line = "/theme" if not str(args or "").strip() else f"/theme {args.strip()}"
        body = self._capture_cli_chat_ui_text(
            handle_theme,
            line=line,
            data_root=resolve_theme_data_root(self._runtime),
            theme_applier=self.app.apply_theme,
            active_theme_name_getter=lambda: self.app.active_theme.name,
        )
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=body or "Theme information unavailable.",
            )
        )

    def _slash_debug(self, _args: str) -> None:
        self.action_toggle_debug()

    def _slash_quiet(self, _args: str) -> None:
        """Switch the transcript to quiet mode."""
        self._apply_session_verbosity("quiet")

    def _slash_normal(self, _args: str) -> None:
        """Switch the transcript to normal mode."""
        self._apply_session_verbosity("normal")

    def _slash_verbose(self, _args: str) -> None:
        """Switch the transcript to verbose mode."""
        self._apply_session_verbosity("verbose")

    def _slash_details(self, args: str) -> None:
        level, message = resolve_details_mode(self._verbosity, args)
        self._apply_session_verbosity(level)
        self._push_system_body(f"details → {message}")

    def _apply_session_verbosity(self, level: str) -> None:
        """Apply a session verbosity override."""
        if level not in ("quiet", "normal", "verbose"):
            return
        self._verbosity = level
        try:
            self.query_one(FocusTranscript).set_verbosity(level)  # type: ignore[arg-type]
        except (QueryError, AttributeError):
            pass
        try:
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"verbosity → {level}",
                )
            )
        except (QueryError, AttributeError):
            pass

    def _slash_model(self, args: str) -> None:
        """Show or switch the current provider/model."""
        provider = self._runtime_provider_name() or "(unknown)"
        model = self._runtime_model_name() or "(unknown)"
        arg = str(args or "").strip()
        if not arg:
            lister = getattr(self._runtime, "list_models", None)
            rows: list[tuple[str, str, bool]] = []
            if callable(lister):
                try:
                    rows = list(lister() or [])
                except Exception:
                    rows = []
            lines = [
                f"current    {provider}/{model}" if model else f"current    {provider}"
            ]
            if rows:
                lines.append("")
                lines.append("configured providers:")
                for name, configured_model, is_active in rows:
                    marker = "◆" if is_active else " "
                    lines.append(
                        f"  {marker} {name:<12} {configured_model or '(none)'}"
                    )
                lines.append("")
                lines.append(
                    "Switch with `/model <provider>` or "
                    "`/model <provider>/<model>` (session-scoped)."
                )
            else:
                lines.append("(no providers configured)")
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="\n".join(lines),
                )
            )
            return
        switcher = getattr(self._runtime, "switch_model", None)
        if not callable(switcher):
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="(/model: runtime does not expose switch_model)",
                )
            )
            return
        try:
            new_provider, new_model = switcher(arg)
        except ValueError as exc:
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"/model: {exc}",
                )
            )
            return
        label = (
            f"{new_provider}/{new_model}"
            if new_model
            else (new_provider or "(default)")
        )
        self.query_one(FocusTranscript).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"model → {label} (session-scoped; restart reverts)",
            )
        )

    def _slash_cost(self, _args: str) -> None:
        """Print current session token / cost usage."""
        snapshot_getter = getattr(self._runtime, "token_usage_snapshot", None)
        snap = None
        if callable(snapshot_getter):
            try:
                snap = snapshot_getter()
            except (AttributeError, TypeError, ValueError):
                snap = None
        if snap is None or not getattr(snap, "has_any_usage", False):
            body = "No token / cost usage data available for this session."
        else:
            session_total = getattr(snap, "session_total_tokens", None)
            turn_total = getattr(snap, "turn_total_tokens", None)
            context_used = getattr(snap, "context_used_tokens", None)
            context_limit = getattr(snap, "context_limit_tokens", None)
            cost_usd = getattr(snap, "cost_usd", None)
            lines = ["Session usage:"]
            if session_total is not None:
                lines.append(f"  session tokens   {session_total}")
            if turn_total is not None:
                lines.append(f"  last turn        {turn_total}")
            if context_used is not None and context_limit:
                pct = snap.context_pct
                pct_str = f"  ({pct}%)" if pct is not None else ""
                lines.append(
                    f"  context window   {context_used}/{context_limit}{pct_str}"
                )
            if cost_usd is not None:
                try:
                    lines.append(f"  estimated cost   ${float(cost_usd):.4f}")
                except (TypeError, ValueError):
                    pass
            body = "\n".join(lines)
        self.query_one(FocusTranscript).push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def _push_system_body(self, body: str) -> None:
        self.query_one(FocusTranscript).push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def _slash_context(self, _args: str) -> None:
        from openminion.cli.presentation.visible_parity import render_context_report

        self._push_system_body(render_context_report(self._runtime))

    def _slash_memory(self, _args: str) -> None:
        from openminion.cli.presentation.visible_parity import render_memory_report

        self._push_system_body(render_memory_report(self._runtime))

    def _slash_skills(self, _args: str) -> None:
        from openminion.cli.presentation.visible_parity import render_skills_report

        self._push_system_body(render_skills_report(self._runtime))

    def _slash_effort(self, args: str) -> None:
        from openminion.cli.presentation.visible_parity import handle_effort_command

        self._push_system_body(handle_effort_command(self._runtime, args))
        self._push_status_line()

    def _slash_statusline(self, args: str) -> None:
        from openminion.cli.presentation.visible_parity import (
            handle_statusline_command,
        )

        self._push_system_body(handle_statusline_command(self._runtime, args))
        self._push_status_line()

    def _slash_undo(self, args: str) -> None:
        from openminion.cli.presentation.visible_parity import handle_undo_command

        self._push_system_body(
            handle_undo_command(self._runtime, args, working_dir=self._working_dir)
        )
        self._load_history()

    def _slash_permissions(self, args: str) -> None:
        """Show or set the session-scoped permission mode."""
        arg = str(args or "").strip().lower()
        if not arg:
            self._open_permissions_overlay()
            return
        if arg == "cycle":
            body = f"permissions → {self._cycle_permission_mode_from_ui()}"
        else:
            setter = getattr(self._runtime, "set_permission_mode", None)
            if not callable(setter):
                body = "(/permissions: runtime does not expose set_permission_mode)"
            else:
                try:
                    body = f"permissions → {setter(arg)}"
                    if arg == "bypass":
                        body = (
                            f"{body} — full access for this session; "
                            "use `/permissions` for the safer chooser."
                        )
                except ValueError as exc:
                    body = f"/permissions: {exc}"
        self._push_status_line()
        self.query_one(FocusTranscript).push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def _open_permissions_overlay(self) -> bool:
        def _on_selected(result: tuple[str, bool] | None) -> None:
            if result is None:
                return
            choice_id, confirmed = result
            self._apply_permission_menu_choice(choice_id, confirmed=confirmed)

        try:
            self.app.push_screen(PermissionsOverlay(), _on_selected)
        except (AttributeError, QueryError, RuntimeError, ValueError) as exc:
            self._push_permissions_message(
                f"/permissions: unable to open chooser: {exc}"
            )
            return False
        return True

    def _apply_permission_menu_choice(self, choice_id: str, *, confirmed: bool) -> None:
        try:
            result = apply_permission_menu_choice(
                self._runtime,
                choice_id,
                confirmed=confirmed,
            )
        except (PermissionError, RuntimeError, ValueError) as exc:
            body = f"/permissions: {exc}"
        else:
            body = result.message
        self._push_status_line()
        self._push_permissions_message(body)

    def _push_permissions_message(self, body: str) -> None:
        try:
            self.query_one(FocusTranscript).push_message(
                ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
            )
        except (QueryError, AttributeError):
            pass

    def _slash_diff(self, args: str) -> None:
        """Show the current git diff in the focus transcript."""

        from openminion.cli.presentation.git.diff import render_git_diff

        chat = self.query_one(FocusTranscript)
        try:
            result = render_git_diff(self._working_dir, args)
        except ValueError as exc:
            body = f"/diff: {exc}"
        else:
            body = result.display_body
        chat.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def _cycle_permission_mode_from_ui(self) -> str:
        cycler = getattr(self._runtime, "cycle_permission_mode", None)
        if not callable(cycler):
            return "unavailable"
        mode = str(cycler() or "default")
        self._push_status_line()
        return mode

    def action_cycle_permission_mode(self) -> None:
        opener = getattr(self, "_open_permissions_overlay", None)
        if callable(opener):
            if not opener():
                return
        else:
            self._cycle_permission_mode_from_ui()
            return
        mode = format_permission_status_label(
            permission_mode=getattr(self._runtime, "permission_mode", "default"),
            action_policy_mode=getattr(
                self._runtime, "action_policy_mode_override", None
            ),
        ) or str(getattr(self._runtime, "permission_mode", "default") or "default")
        try:
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"permissions chooser opened (current: {mode})",
                )
            )
        except (QueryError, AttributeError):
            pass

    def _slash_compact(self, _args: str) -> None:
        """Compact conversation history (if the runtime supports it)."""
        compact_fn = getattr(self._runtime, "compact_history", None)
        if not callable(compact_fn):
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="Compaction is not supported by the current runtime.",
                )
            )
            return
        try:
            result = compact_fn()
        except Exception as exc:
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"Compaction failed: {exc}",
                )
            )
            return
        self._load_history()
        suffix = ""
        if isinstance(result, dict):
            new_total = result.get("session_total_tokens") or result.get(
                "session_total"
            )
            if new_total is not None:
                suffix = f" — session tokens now {new_total}"
        self.query_one(FocusTranscript).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"Conversation compacted{suffix}.",
            )
        )

    def _slash_queue(self, args: str) -> None:
        owner = cast(Any, self)
        parts = str(args or "").strip().split()
        entries = owner._turn_input_queue.list_entries(
            session_id=owner._runtime.session_id,
            agent_id=owner._runtime.agent_id,
            statuses={TurnInputQueueStatus.QUEUED},
        )
        if not parts:
            self._push_system_body(queue_listing([entry.text for entry in entries]))
            return
        action = parts[0].lower()
        if action == "clear":
            count = 0
            for entry in entries:
                owner._turn_input_queue.drop(
                    session_id=entry.session_id,
                    queue_id=entry.queue_id,
                    status_version=entry.status_version,
                )
                count += 1
            self._push_system_body(queue_cleared_notice(count))
            owner._push_status_line()
            return
        if action == "drop":
            if len(parts) < 2:
                self._push_system_body(queue_drop_usage_notice())
                return
            try:
                index = int(parts[1])
            except ValueError:
                self._push_system_body(queue_drop_usage_notice())
                return
            if index < 1 or index > len(entries):
                self._push_system_body(queue_drop_missing_notice(index))
                return
            entry = entries[index - 1]
            owner._turn_input_queue.drop(
                session_id=entry.session_id,
                queue_id=entry.queue_id,
                status_version=entry.status_version,
            )
            self._push_system_body(queue_drop_notice(index, entry.text))
            owner._push_status_line()
            return
        if action == "run-next":
            owner.run_worker(owner._cancel_current_and_run_next(), exclusive=False)
            return
        self._push_system_body(queue_command_usage_notice())

    def _slash_resume(self, _args: str) -> None:
        """Open the session picker pre-filtered to non-empty sessions."""
        lister = getattr(self._runtime, "list_directory_sessions", None)
        if not callable(lister):
            self.action_show_sessions()
            return
        try:
            sessions = list(lister(limit=50) or [])
        except Exception:
            sessions = []
        non_empty = [
            s for s in sessions if int(getattr(s, "message_count", 0) or 0) > 0
        ]
        if not non_empty:
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=(
                        "No prior sessions with messages found in this "
                        "directory. Use `/new` to start one."
                    ),
                )
            )
            return
        self._open_session_picker(non_empty)

    def _open_session_picker(self, sessions: list) -> None:
        """Open the resume picker screen for the provided sessions."""
        try:
            from .widgets.resume_picker import (
                ResumePickerScreen,
                build_resume_dicts,
            )

            session_dicts = build_resume_dicts(sessions)
            if not session_dicts:
                self.action_show_sessions()
                return

            def _on_pick(chosen: str | None) -> None:
                if not chosen:
                    return
                try:
                    self._runtime.bind_session(str(chosen))
                except Exception:
                    return
                self._load_history()
                self._refresh_header(status_mode="idle")

            self.app.push_screen(ResumePickerScreen(session_dicts), _on_pick)
        except Exception:
            self.action_show_sessions()

    def _slash_status(self, _args: str) -> None:
        self.query_one(FocusTranscript).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=(
                    f"agent      {self._runtime.agent_id}\n"
                    f"provider   {self._runtime_provider_name()}\n"
                    f"model      {self._runtime_model_name()}\n"
                    f"session    {self._runtime.session_id}\n"
                    f"dir        {self._working_dir}\n"
                    f"transport  {self._runtime.transport}"
                ),
            )
        )

    @staticmethod
    def _capture_cli_chat_ui_text(callback, /, *args, **kwargs) -> str:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            callback(*args, **kwargs)
        return buffer.getvalue().strip()

    def _slash_exit(self, _args: str) -> None:
        self.run_worker(self._confirm_exit(), exclusive=False)

    async def _run_init_command(self) -> None:
        chat = self.query_one(FocusTranscript)
        should_write = await self._ask_inline(
            f"Create OPENMINION.md in {self._working_dir}?",
            kind="init",
        )
        if not should_write:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="Init cancelled.",
                )
            )
            return
        try:
            target_path = write_init_template(
                working_dir=self._working_dir,
                agent_id=str(getattr(self._runtime, "agent_id", "") or "openminion"),
            )
        except FileExistsError as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"Project context file already exists: {exc}",
                )
            )
            return
        except (OSError, TypeError, ValueError) as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.ERROR,
                    sender="error",
                    body=f"Could not write OPENMINION.md: {exc}",
                )
            )
            return
        setter = getattr(self._runtime, "set_project_context", None)
        if callable(setter):
            try:
                setter(resolve_project_context(self._working_dir))
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"Wrote {target_path}. Restart or start a new session to load it.",
            )
        )

    def _slash_dashboard(self, _args: str) -> None:
        from openminion.cli.commands.tui import dashboard_deprecation_message
        from openminion.cli.status.surface import record_surface_event

        notice = dashboard_deprecation_message()
        if notice:
            self._push_system_body(notice)
            record_surface_event(
                self._runtime,
                surface="dashboard",
                action="deprecation",
            )
        record_surface_event(
            self._runtime,
            surface="dashboard",
            action="launch",
        )
        try:
            from openminion.cli.tui.screen import MainScreen
        except ImportError as exc:  # pragma: no cover
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=(
                        f"Dashboard unavailable: {exc}. Stay in Focus or "
                        "run `openminion dashboard` from another terminal."
                    ),
                )
            )
            return
        try:
            self.app.push_screen(MainScreen(runtime=self._runtime))
        except Exception as exc:
            self.query_one(FocusTranscript).push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=(
                        f"Dashboard side-trip not available in this "
                        f"context ({exc}). Use `openminion dashboard` from "
                        "another terminal for the full dashboard view."
                    ),
                )
            )

    def _slash_copy(self, _args: str) -> None:
        """Mirror the Ctrl+Y copy action for terminals that capture the key."""
        self.action_copy_last_agent()

    def _slash_export(self, _args: str) -> None:
        session_id = str(getattr(self._runtime, "session_id", "") or "").strip()
        if session_id:
            command = (
                f"openminion export transcript --session-id {session_id} --format md"
            )
        else:
            command = (
                "openminion export transcript --session-id <session-id> --format md"
            )
        self._push_system_body(
            "Export the current transcript from a regular terminal:\n"
            f"  {command}\n"
            "Add `--output transcript.md` to write a file."
        )

    def _slash_editor(self, _args: str) -> None:
        self._push_system_body(
            "External-editor composition is not bound in rich focus yet. "
            "Use multiline input, paste content, or @-mention files."
        )

    def _slash_agent(self, args: str) -> None:
        """List agents or switch to one by id."""
        chat = self.query_one(FocusTranscript)
        runtime = self._runtime
        target = args.strip()
        try:
            agents = list(runtime.list_agents() or [])
        except Exception as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"Could not list agents: {exc}",
                )
            )
            return
        active_id = str(getattr(runtime, "agent_id", "") or "").strip()
        if not target:
            if not agents:
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body="No agents registered.",
                    )
                )
                return
            lines = ["Agents:"]
            for entry in agents:
                aid = str(getattr(entry, "id", entry)).strip() or "?"
                marker = "● " if aid == active_id else "  "
                lines.append(f"  {marker}{aid}")
            lines.append("")
            lines.append("Use `/agent <id>` to switch.")
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body="\n".join(lines),
                )
            )
            return
        known = {str(getattr(entry, "id", entry)).strip() for entry in agents}
        if target not in known:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=(
                        f"Unknown agent: {target!r}. "
                        f"Use bare `/agent` to list registered agents."
                    ),
                )
            )
            return
        try:
            runtime.switch_agent(target)
        except Exception as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"Could not switch agent: {exc}",
                )
            )
            return
        self._tool_widgets.clear()
        chat.clear_messages()
        if not bool(getattr(runtime, "is_bound", False)):
            try:
                creator = getattr(runtime, "create_new_session", None)
                if callable(creator):
                    creator()
            except Exception as exc:
                self._refresh_header()
                chat.push_message(
                    ChatMessage(
                        kind=MessageKind.SYSTEM,
                        sender="system",
                        body=(
                            f"Switched to agent {target}, but could not "
                            f"create a new session: {exc}"
                        ),
                    )
                )
                return
        self._refresh_header()
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"Switched to agent {target}.",
            )
        )

    def _slash_help(self, _args: str) -> None:
        """Rich help — one row per registered command with description."""
        rows = []
        for aliases, description, _handler in self._slash_command_registry:
            primary = aliases[0]
            extra = f"  (also {', '.join(aliases[1:])})" if len(aliases) > 1 else ""
            rows.append(f"  {primary:<11}— {description}{extra}")
        body = "Slash commands:\n" + "\n".join(rows)
        body += "\n\nKeys: Ctrl+P palette  Ctrl+F search  Ctrl+N new  Ctrl+T tools"
        self.query_one(FocusTranscript).push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=body,
            )
        )
