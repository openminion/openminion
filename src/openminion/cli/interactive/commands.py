from __future__ import annotations

from argparse import Namespace
import contextlib
import io
import shlex
from typing import Any, cast

from textual.css.query import QueryError

from openminion.cli.presentation.animation import (
    AnimationRegistry,
    AnimationSelectionError,
    AnimationSpecError,
    default_animation_registry,
    parse_animation_token,
    resolve_focus_animation,
)
from openminion.cli.interactive.project_context import (
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
from openminion.cli.ux.verbosity import write_focus_preferences
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

    def _slash_tools(self, args: str) -> None:
        text = "/tools" if not str(args or "").strip() else f"/tools {args.strip()}"
        body = self._tools_command_body(text)
        self.query_one(FocusTranscript).push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def _tools_command_body(self, text: str) -> str:
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return f"Invalid /tools command: {exc}"
        action = parts[1].lower() if len(parts) > 1 else "status"
        if action == "list":
            tools = self._runtime.list_tools()
            return (
                "\n".join(
                    f"{'✓' if enabled else '✗'}  {name}" for name, enabled in tools
                )
                or "(none)"
            )
        if action == "status":
            snapshot = self._runtime.tool_exposure_status()
            rows = [
                "  ".join(
                    (
                        "active" if profile.get("active") else "hidden",
                        str(profile.get("profile_id", "")),
                        f"({profile.get('tier', '')})",
                    )
                )
                for profile in snapshot.get("profiles", [])
            ]
            return "Tool exposure profiles:\n" + ("\n".join(rows) or "(none)")
        if action not in {"activate", "deactivate"} or len(parts) < 3:
            return (
                "Usage: /tools [status|list]\n"
                "       /tools activate <profile> [key=value ...]\n"
                "       /tools deactivate <profile> [target=<id>]"
            )
        profile_id = parts[2]
        try:
            options = dict(token.split("=", 1) for token in parts[3:])
        except ValueError:
            return "Tool profile options must use key=value syntax."
        target_id = options.get("target", "")
        if action == "deactivate":
            changed = self._runtime.deactivate_tool_profile(
                profile_id,
                target_id=target_id,
            )
            return f"{'Deactivated' if changed else 'Not active'}: {profile_id}"
        approved = options.get("approved", "").lower() in {"1", "true", "yes"}
        try:
            activation = self._runtime.activate_tool_profile(
                profile_id,
                target_id=target_id,
                target_kind=options.get("target_kind", ""),
                credential_scopes=self._option_tokens(options.get("credential", "")),
                dependencies=self._option_tokens(options.get("dependency", "")),
                approved=approved,
                ttl_seconds=(float(options["ttl"]) if options.get("ttl") else None),
                activation_reason=options.get("reason", ""),
                approved_by=options.get("approved_by", ""),
                policy_source=options.get("policy_source", ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return f"Activation denied: {exc}"
        return f"Activated: {activation['profile_id']} ({activation['audit_id']})"

    @staticmethod
    def _option_tokens(value: str) -> tuple[str, ...]:
        return tuple(token.strip() for token in value.split(",") if token.strip())

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

    def _slash_animation(self, args: str) -> None:
        parts = str(args or "").strip().split()
        sub = parts[0].lower() if parts else ""
        chat = self.query_one(FocusTranscript)

        if not sub:
            self._push_animation_status(chat)
            return
        if sub == "list":
            self._push_animation_list(chat, default_animation_registry())
            return
        if sub == "reset":
            self._reset_animation(chat)
            return
        if sub not in {"use", "save"} or len(parts) < 2:
            self._push_animation_usage(chat)
            return
        self._apply_animation_command(sub, parts[1], chat)

    def _push_animation_status(self, chat: FocusTranscript) -> None:
        resolution = self._animation_resolution
        spec = resolution.spec
        lines = [
            "Animation settings:",
            f"  active     {spec.provider_id}:{spec.name}",
            f"  interval   {spec.interval_ms}ms",
            f"  source     {resolution.source}",
            f"  progress   {self._progress}",
        ]
        if resolution.is_fallback:
            lines.append(f"  fallback   {resolution.fallback_reason}")
        chat.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body="\n".join(lines))
        )

    def _push_animation_list(
        self,
        chat: FocusTranscript,
        registry: AnimationRegistry,
    ) -> None:
        lines = ["Available animations:"]
        for provider_id in registry.provider_ids(discover=True):
            try:
                names = registry.names(provider_id, discover=False)
            except AnimationSpecError as exc:
                lines.append(f"  {provider_id}: unavailable ({exc})")
                continue
            for name in names:
                lines.append(f"  {provider_id}:{name}")
        for diagnostic in registry.diagnostics:
            lines.append(f"  ! {diagnostic.render()}")
        chat.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body="\n".join(lines))
        )

    def _reset_animation(self, chat: FocusTranscript) -> None:
        path = write_focus_preferences({"animation_provider": None, "animation": None})
        self._apply_animation_resolution(
            resolve_focus_animation(Namespace(animation_provider=None, animation=None))
        )
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"animation reset to {self._animation_label()} ({path})",
            )
        )

    def _push_animation_usage(self, chat: FocusTranscript) -> None:
        body = (
            "usage: /animation, /animation list, "
            "/animation use <provider:preset>, "
            "/animation save <provider:preset>, /animation reset"
        )
        chat.push_message(
            ChatMessage(kind=MessageKind.SYSTEM, sender="system", body=body)
        )

    def _apply_animation_command(
        self,
        subcommand: str,
        token: str,
        chat: FocusTranscript,
    ) -> None:
        provider_id, name = parse_animation_token(token)
        try:
            resolution = resolve_focus_animation(
                Namespace(animation_provider=provider_id, animation=name)
            )
        except AnimationSelectionError as exc:
            chat.push_message(
                ChatMessage(
                    kind=MessageKind.SYSTEM,
                    sender="system",
                    body=f"/animation: {exc}",
                )
            )
            return
        self._apply_animation_resolution(resolution)
        action = "animation"
        if subcommand == "save":
            path = write_focus_preferences(
                {
                    "animation_provider": resolution.spec.provider_id,
                    "animation": resolution.spec.name,
                }
            )
            action = f"animation saved to {path}"
        chat.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=f"{action} → {self._animation_label()}",
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

    def _slash_tasks(self, args: str) -> None:
        from openminion.cli.presentation.visible_parity import render_tasks_report

        self._push_system_body(render_tasks_report(self._runtime, args))

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
        """Show the current git diff in the interactive transcript."""

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
        from openminion.cli.commands.aliases import dashboard_deprecation_message
        from openminion.cli.status.surface import record_surface_event

        notice = dashboard_deprecation_message()
        self._push_system_body(notice)
        record_surface_event(
            self._runtime,
            surface="dashboard",
            action="deprecation",
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
            "External-editor composition is not bound in the rich renderer yet. "
            "Use multiline input, paste content, or @-mention files."
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
