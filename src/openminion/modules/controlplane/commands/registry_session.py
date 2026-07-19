# mypy: ignore-errors
from __future__ import annotations

from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)


class CommandRegistrySessionMixin:
    def _status(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            turns = self._list_turns(ctx.session_id)
            profile_id = self.store.resolve_agent(ctx.session_id)
            title = (
                self.store.get_session_title(ctx.session_id)
                if hasattr(self.store, "get_session_title")
                else None
            )
            pairing = self._current_pairing(ctx)
            pairing_status = (
                str(pairing.get("status") or "active") if pairing else "not observed"
            )
            lines = [
                "Status:",
                "  runner: online from this chat if replies are arriving; otherwise not observed from this process",
                f"  profile: {profile_id}",
                f"  session: {ctx.session_id}",
                f"  turns: {len(turns)}",
                f"  pairing: {pairing_status}",
                "  access: broad non-admin controlplane access until ACL exists",
            ]
            if title:
                lines.insert(4, f"  title: {title}")
            return CommandResult(
                ok=True,
                text="\n".join(lines),
                data={
                    "session_id": ctx.session_id,
                    "agent_id": profile_id,
                    "profile_id": profile_id,
                    "turn_count": len(turns),
                    "pairing_status": pairing_status,
                },
            )

    def _sessions(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if not hasattr(self.store, "list_sessions"):
                return CommandResult(
                    ok=True,
                    text="Sessions listing is not available in this backend.",
                    data={"sessions": []},
                )
            sessions = self.store.list_sessions(ctx.user_key, ctx.chat_key)
            if not sessions:
                return CommandResult(
                    ok=True, text="No sessions yet.", data={"sessions": []}
                )
            lines = ["Sessions:"]
            for item in sessions:
                sid = item.get("session_id", "")
                title = item.get("title")
                suffix = f" — {title}" if title else ""
                lines.append(f"  {sid}{suffix}")
            return CommandResult(
                ok=True, text="\n".join(lines), data={"sessions": sessions}
            )

    def _session_use(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            if not command.args:
                return CommandResult(ok=False, text="Usage: /session use <session_id>")
            session_id = command.args[0].strip()
            if not session_id:
                return CommandResult(ok=False, text="Usage: /session use <session_id>")
            is_admin = bool(
                self.auth is not None
                and hasattr(self.auth, "is_admin")
                and self.auth.is_admin(ctx.user_key)
            )
            owner = (
                self.store.session_owner(session_id)
                if hasattr(self.store, "session_owner")
                else None
            )
            if hasattr(self.store, "bind_session_owned"):
                allowed = self.store.bind_session_owned(
                    user_key=ctx.user_key,
                    chat_key=ctx.chat_key,
                    session_id=session_id,
                    is_admin=is_admin,
                )
                if not allowed:
                    reason = "missing_session" if owner is None else "owner_mismatch"
                    self._emit_audit(
                        "session.bind.denied",
                        user_key=ctx.user_key,
                        chat_key=ctx.chat_key,
                        requested_session_id=session_id,
                        owner_user_key=owner,
                        reason=reason,
                    )
                    return CommandResult(
                        ok=False,
                        text=f"Session {session_id} not found or not yours",
                        error={"code": "SESSION_BIND_DENIED", "reason": reason},
                    )
                if owner is not None and owner != ctx.user_key and is_admin:
                    self._emit_audit(
                        "session.bind.admin_override",
                        user_key=ctx.user_key,
                        chat_key=ctx.chat_key,
                        requested_session_id=session_id,
                        owner_user_key=owner,
                    )
            elif hasattr(self.store, "bind_session"):
                self.store.bind_session(ctx.user_key, ctx.chat_key, session_id)
            return CommandResult(
                ok=True,
                text=f"Now using session {session_id}",
                data={"session_id": session_id},
            )

    def _session_title(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            title = " ".join(command.args).strip()
            if not title:
                return CommandResult(ok=False, text="Usage: /session title <text>")
            if hasattr(self.store, "set_session_title"):
                self.store.set_session_title(ctx.session_id, title)
            return CommandResult(
                ok=True,
                text=f"Session {ctx.session_id} title set.",
                data={"session_id": ctx.session_id, "title": title},
            )

    def _export(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            fmt = command.args[0].lower() if command.args else "md"
            if fmt not in {"md", "json"}:
                return CommandResult(ok=False, text="Usage: /export [md|json]")
            turns = self._list_turns(ctx.session_id)
            if fmt == "json":
                return CommandResult(
                    ok=True,
                    text=f"Exported {len(turns)} turns as JSON (stub).",
                    data={"format": fmt, "turns": len(turns)},
                )
            return CommandResult(
                ok=True,
                text=f"Exported {len(turns)} turns as Markdown (stub).",
                data={"format": fmt, "turns": len(turns)},
            )

    def _agent_show(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            agent_id = self.store.resolve_agent(ctx.session_id)
            return CommandResult(
                ok=True,
                text=f"Current profile: {agent_id}",
                data={"agent_id": agent_id, "profile_id": agent_id},
            )

    def _agent_ls(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            agents = self.store.list_agents()
            lines = [f"  {a['id']}: {a.get('name', '')}" for a in agents]
            return CommandResult(
                ok=True,
                text="Configured profiles:\n" + "\n".join(lines),
                data={"agents": agents, "profiles": agents},
            )

    def _agent_use(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
            if not command.args:
                return CommandResult(ok=False, text="Usage: /profile use <profile_id>")
            agent_id = command.args[0]
            self.store.ensure_agent(agent_id)
            self.store.set_agent(ctx.session_id, agent_id)
            return CommandResult(
                ok=True,
                text=(
                    f"Session {ctx.session_id} now uses profile {agent_id}. "
                    "Context is preserved; use /session new for a fresh context."
                ),
                data={"agent_id": agent_id, "profile_id": agent_id},
            )

    def _agent_info(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            target = command.args[0] if command.args else ctx.agent_id
            agents = {a["id"]: a for a in self.store.list_agents()}
            if target not in agents:
                return CommandResult(ok=False, text=f"Profile not found: {target}")
            info = agents[target]
            data = dict(info)
            data.setdefault("profile_id", target)
            return CommandResult(ok=True, text=f"Profile {target}: {info}", data=data)

    def _agent_stop(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            return CommandResult(
                ok=True,
                text=(
                    f"Profile {ctx.agent_id} stopped for session {ctx.session_id}. "
                    "Use /session new to start fresh."
                ),
                data={
                    "session_id": ctx.session_id,
                    "agent_id": ctx.agent_id,
                    "profile_id": ctx.agent_id,
                },
            )

    def _session_new(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            new_session = self.store.rebind_session(ctx.user_key, ctx.chat_key)
            return CommandResult(
                ok=True,
                text=f"Started new session {new_session}",
                data={"session_id": new_session},
            )

    def _session_id(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            return CommandResult(
                ok=True,
                text=f"Current session: {ctx.session_id}",
                data={"session_id": ctx.session_id},
            )

    def _session_status(
            self, command: ParsedCommand, ctx: ResolvedContext
        ) -> CommandResult:
            turns = self._list_turns(ctx.session_id)
            profile_id = self.store.resolve_agent(ctx.session_id)
            return CommandResult(
                ok=True,
                text=(
                    f"Session {ctx.session_id}: profile={profile_id}, turns={len(turns)}"
                ),
                data={
                    "session_id": ctx.session_id,
                    "agent_id": profile_id,
                    "profile_id": profile_id,
                    "turn_count": len(turns),
                },
            )


