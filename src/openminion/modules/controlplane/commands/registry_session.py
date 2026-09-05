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
        access = (
            self._describe_scopes(pairing.get("scopes") or [])
            if pairing
            else "not observed"
        )
        lines = [
            "Controlplane status:",
            "  channel: online (this reply confirms the controlplane path)",
            f"  profile: {profile_id}",
            f"  session: {ctx.session_id}",
            f"  turns: {len(turns)}",
            f"  pairing: {pairing_status}",
            f"  access: {access}",
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
            return self._feature_unavailable("Session listing", data={"sessions": []})
        sessions = self.store.list_sessions(ctx.user_key, ctx.chat_key)
        if not sessions:
            return CommandResult(
                ok=True, text="No sessions yet.", data={"sessions": []}
            )
        lines = ["Sessions for this chat:"]
        for item in sessions:
            sid = item.get("session_id", "")
            title = item.get("title")
            marker = " (current)" if sid == ctx.session_id else ""
            suffix = f" - {title}" if title else ""
            lines.append(f"  {sid}{marker}{suffix}")
        lines.append(
            "Use /session use <session_id> to switch, or /session new to start fresh."
        )
        return CommandResult(
            ok=True, text="\n".join(lines), data={"sessions": sessions}
        )

    def _session_use(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        session_id = command.args[0].strip() if command.args else ""
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
            text=f"Now using session {session_id}. Existing context restored.",
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
        return self._feature_unavailable(
            "Session export",
            data={"format": fmt, "session_id": ctx.session_id},
        )

    def _agent_show(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        agent_id = self.store.resolve_agent(ctx.session_id)
        return CommandResult(
            ok=True,
            text=(
                f"Current profile: {agent_id}\n"
                f"Session: {ctx.session_id}\n"
                "Use /profile list to see available profiles."
            ),
            data={"agent_id": agent_id, "profile_id": agent_id},
        )

    def _agent_ls(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        agents = self.store.list_agents()
        current = self.store.resolve_agent(ctx.session_id)
        lines = ["Configured profiles:"]
        for agent in agents:
            profile_id = agent["id"]
            name = agent.get("name")
            marker = " (current)" if profile_id == current else ""
            label = f" - {name}" if name and name != profile_id else ""
            lines.append(f"  {profile_id}{marker}{label}")
        lines.append("Use /profile use <profile_id> to switch this session.")
        return CommandResult(
            ok=True,
            text="\n".join(lines),
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
        name = info.get("name") or target
        return CommandResult(
            ok=True,
            text=f"Profile: {target}\n  name: {name}",
            data=data,
        )

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
        profile_id = self.store.resolve_agent(new_session)
        return CommandResult(
            ok=True,
            text=(
                f"Started new session {new_session} with fresh context.\n"
                f"Profile: {profile_id}"
            ),
            data={
                "session_id": new_session,
                "agent_id": profile_id,
                "profile_id": profile_id,
            },
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
        title = self.store.get_session_title(ctx.session_id)
        lines = [
            "Current session:",
            f"  id: {ctx.session_id}",
            f"  profile: {profile_id}",
            f"  turns: {len(turns)}",
        ]
        if title:
            lines.insert(2, f"  title: {title}")
        return CommandResult(
            ok=True,
            text="\n".join(lines),
            data={
                "session_id": ctx.session_id,
                "agent_id": profile_id,
                "profile_id": profile_id,
                "turn_count": len(turns),
            },
        )
