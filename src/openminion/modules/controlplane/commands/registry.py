from dataclasses import dataclass
from openminion.base.logging import get_logger
from typing import Any
from typing import Dict, List, Optional

from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime.audit import emit_audit_event
from openminion.modules.controlplane.runtime.client import RuntimeClient
from openminion.modules.controlplane.contracts.policy_client import PolicyClient
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore
from .module import CommandSpec, CommandSchema, AuthRequirement, Handler
from .broken_module import BrokenModuleTracker

_COMMAND_HELP: dict[str, str] = {
    "help": "Show available commands",
    "new": "Start a fresh session",
    "sessions": "List sessions for this chat/user",
    "session.use": "Rebind this chat to an existing session: /session use <session_id>",
    "session.title": "Set current session title: /session title <text>",
    "export": "Export current session: /export [md|json]",
    "agent": "Show current agent",
    "agent.list": "List registered agents",
    "agent.set": "Switch session to an agent: /agent set <agent_id>",
    "agent.ls": "List registered agents",
    "agent.use": "Switch session to an agent: /agent use <agent_id>",
    "agent.info": "Show details for an agent: /agent info <agent_id>",
    "agent.stop": "Stop current agent session",
    "session.new": "Start a fresh session",
    "session.id": "Show current session ID",
    "session.status": "Show session summary",
    "run": "Show active runs",
    "run.status": "Show run status: /run status <run_id>",
    "cancel": "Cancel run: /cancel <run_id>",
    "job.ls": "List active runs (compatibility alias)",
    "approve": "[admin] Approve policy request: /approve <request_id> [once|10m|1h|forever]",
    "deny": "[admin] Deny policy request: /deny <request_id>",
    "grants": "List active grants",
    "diag": "Show high-level health",
    "logs": "Show run log summary: /logs <run_id>",
    "artifact.ls": "List recent artifacts (stub)",
    "memory.ls": "List memory candidates (stub)",
    "config.show": "Show current config (stub)",
    "config.set": "[admin] Set a config value: /config set <key> <value>",
    "artifact.purge": "[admin] Purge deleted artifacts",
    "memory.promote": "[admin] Promote a memory candidate",
    "skill.ingest": "Ingest a SKILL.md file: /skill ingest <path>",
    "skill.list": "List ingested skills",
    "modules": "Show loaded/shadowed/broken module diagnostics",
}
_LOGGER = get_logger("modules.controlplane.commands.registry")


@dataclass
class CommandRegistry:
    store: InMemoryControlPlaneStore
    auth: object | None = None
    audit_logger: object | None = None
    runtime_client: RuntimeClient | None = None
    policy_client: PolicyClient | None = None
    memory_client: Any | None = None

    def __post_init__(self) -> None:
        self._handlers: Dict[str, Handler] = {}
        self._command_specs: Dict[str, CommandSpec] = {}
        self.shadowed_commands: Dict[str, CommandSpec] = {}
        self.loaded_modules: Dict[str, str] = {}
        self.broken_module_tracker: BrokenModuleTracker = BrokenModuleTracker()
        self._register_builtin_commands()

    def _register_builtin_commands(self) -> None:
        """Register all built-in commands as CommandSpecs."""
        handlers_map: Dict[str, Handler] = {
            "help": self._help,
            "new": self._session_new,
            "sessions": self._sessions,
            "session.use": self._session_use,
            "session.title": self._session_title,
            "export": self._export,
            "agent": self._agent_show,
            "agent.list": self._agent_ls,
            "agent.set": self._agent_use,
            "agent.ls": self._agent_ls,
            "agent.use": self._agent_use,
            "agent.info": self._agent_info,
            "agent.stop": self._agent_stop,
            "session.new": self._session_new,
            "session.id": self._session_id,
            "session.status": self._session_status,
            "run": self._run_show,
            "run.status": self._run_status,
            "cancel": self._cancel_run,
            "job.ls": self._job_ls,
            "approve": self._approve,
            "deny": self._deny,
            "grants": self._grants,
            "diag": self._diag,
            "logs": self._logs,
            "artifact.ls": self._artifact_ls,
            "artifact.purge": self._artifact_purge,
            "memory.ls": self._memory_ls,
            "memory.promote": self._memory_promote,
            "config.show": self._config_show,
            "config.set": self._config_set,
            "skill.ingest": self._skill_ingest,
            "skill.list": self._skill_list,
            "modules": self._modules,
        }

        for cmd_name, handler in handlers_map.items():
            schema = CommandSchema(
                name=cmd_name,
                description=_COMMAND_HELP.get(
                    cmd_name, f"Built-in command: {cmd_name}"
                ),
                usage=f"/{cmd_name} [args...]",
            )
            auth_req = (
                AuthRequirement.ADMIN
                if "[admin]" in _COMMAND_HELP.get(cmd_name, "")
                else AuthRequirement.USER
            )
            spec = CommandSpec(
                name=cmd_name,
                schema=schema,
                handler=handler,
                auth_requirement=auth_req,
                module_name="builtin",
                version="1.0.0",
            )
            self.register_command_spec(spec)

    def register_command_spec(
        self, spec: CommandSpec, skip_collision_check: bool = False
    ) -> bool:
        """Register a command specification with collision handling."""
        if not skip_collision_check and spec.name in self._command_specs:
            original_spec = self._command_specs[spec.name]
            _LOGGER.warning(
                "command shadowed name=%s module=%s existing_module=%s",
                spec.name,
                spec.module_name,
                original_spec.module_name,
            )

            self.shadowed_commands[spec.name] = spec
            return False

        self._command_specs[spec.name] = spec
        self._handlers[spec.name] = spec.handler

        if spec.module_name != "builtin":
            self.loaded_modules[spec.module_name] = spec.version

        return True

    def get_command_spec(self, command_name: str) -> CommandSpec | None:
        """Get command spec by name."""
        return self._command_specs.get(command_name)

    def list_commands(self) -> List[str]:
        """List all registered (non-shadowed) command names."""
        return list(self._command_specs.keys())

    def list_shadowed_commands(self) -> List[str]:
        """List all shadowed command names."""
        return list(self.shadowed_commands.keys())

    def get_all_registered_commands(self) -> List[CommandSpec]:
        """Get all registered command specs (including shadowed ones)."""
        return list(self._command_specs.values()) + list(
            self.shadowed_commands.values()
        )

    def get_command_auth_requirement(self, command_name: str) -> AuthRequirement | None:
        """Get auth requirement for a command."""
        spec = self._command_specs.get(command_name)
        return spec.auth_requirement if spec else None

    def get_loaded_modules(self) -> Dict[str, str]:
        """Get dictionary of loaded modules and their versions."""
        return self.loaded_modules.copy()

    def get_broken_modules(self):
        """Get broken modules helper."""
        return self.broken_module_tracker.get_broken_modules()

    def register_broken_module(
        self,
        module_name: str,
        error: Exception,
        failed_commands: Optional[List[str]] = None,
    ) -> None:
        """Register a module that couldn't be loaded."""
        self.broken_module_tracker.register_broken_module(
            module_name, error, failed_commands
        )

    def is_broken_module(self, module_name: str) -> bool:
        """Check if a module has failed loading."""
        return self.broken_module_tracker.is_broken_module(module_name)

    def list_modules(self) -> dict:
        """Get comprehensive module diagnostics."""
        broken_modules = self.broken_module_tracker.get_broken_modules()
        shadowed_module_names = set(
            spec.module_name for spec in self.shadowed_commands.values()
        )

        return {
            "built_in": ["builtin"],
            "loaded": list(self.loaded_modules.keys()),
            "shadowed": list(shadowed_module_names),
            "broken": list(broken_modules.keys()),
            "module_details": dict(self.loaded_modules),
            "errors": {
                mod_name: {
                    "error_type": info.error_type,
                    "error_message": info.error_message,
                    "failed_commands": info.failed_commands,
                    "timestamp": info.timestamp.isoformat(),
                }
                for mod_name, info in broken_modules.items()
            },
        }

    def execute(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        handler = self._handlers.get(command.canonical)
        if handler is None:
            return CommandResult(
                ok=False,
                text=f"Unknown command: /{command.canonical}. Type /help for available commands.",
            )
        if self.auth is not None and self.auth.is_admin_command(command.canonical):
            allowed, reason = self.auth.check(ctx.user_key, command.canonical)
            if not allowed:
                return CommandResult(
                    ok=False,
                    text=f"Permission denied: {reason}",
                    error={"code": "PERMISSION_DENIED", "reason": reason},
                )
        return handler(command, ctx)

    def _help(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        is_admin = self.auth is not None and self.auth.is_admin(ctx.user_key)
        lines = ["Available commands:"]
        for name, desc in sorted(_COMMAND_HELP.items()):
            if "[admin]" in desc and not is_admin:
                continue
            lines.append(f"  /{name} — {desc}")
        return CommandResult(
            ok=True, text="\n".join(lines), data={"is_admin": is_admin}
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
            ok=True, text=f"Current agent: {agent_id}", data={"agent_id": agent_id}
        )

    def _agent_ls(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        agents = self.store.list_agents()
        lines = [f"  {a['id']}: {a.get('name', '')}" for a in agents]
        return CommandResult(
            ok=True,
            text="Registered agents:\n" + "\n".join(lines),
            data={"agents": agents},
        )

    def _agent_use(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        if not command.args:
            return CommandResult(ok=False, text="Usage: /agent use <agent_id>")
        agent_id = command.args[0]
        self.store.ensure_agent(agent_id)
        self.store.set_agent(ctx.session_id, agent_id)
        return CommandResult(
            ok=True,
            text=f"Session {ctx.session_id} now uses {agent_id}",
            data={"agent_id": agent_id},
        )

    def _agent_info(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        target = command.args[0] if command.args else ctx.agent_id
        agents = {a["id"]: a for a in self.store.list_agents()}
        if target not in agents:
            return CommandResult(ok=False, text=f"Agent not found: {target}")
        info = agents[target]
        return CommandResult(ok=True, text=f"Agent {target}: {info}", data=info)

    def _agent_stop(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        return CommandResult(
            ok=True,
            text=f"Agent {ctx.agent_id} stopped for session {ctx.session_id}. Use /session new to start fresh.",
            data={"session_id": ctx.session_id, "agent_id": ctx.agent_id},
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
        agent_id = self.store.resolve_agent(ctx.session_id)
        return CommandResult(
            ok=True,
            text=f"Session {ctx.session_id}: agent={agent_id}, turns={len(turns)}",
            data={
                "session_id": ctx.session_id,
                "agent_id": agent_id,
                "turn_count": len(turns),
            },
        )

    def _job_ls(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        return CommandResult(ok=True, text="No active jobs.", data={"jobs": []})

    def _run_show(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        if self.runtime_client is None:
            return CommandResult(
                ok=True,
                text="No active runs.",
                data={"runs": [], "note": "runtime not connected"},
            )
        runs = self.runtime_client.list_runs(ctx.session_id)
        if not runs:
            return CommandResult(ok=True, text="No active runs.", data={"runs": []})
        lines = [f"  {r.run_id}: {r.state} (agent={r.agent_id})" for r in runs]
        return CommandResult(
            ok=True,
            text="Active runs:\n" + "\n".join(lines),
            data={
                "runs": [
                    {"run_id": r.run_id, "state": r.state, "agent_id": r.agent_id}
                    for r in runs
                ]
            },
        )

    def _run_status(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        if not command.args:
            return CommandResult(ok=False, text="Usage: /run status <run_id>")
        run_id = command.args[0]
        if self.runtime_client is None:
            return CommandResult(
                ok=True,
                text=f"Run {run_id}: status=unknown (runtime not connected)",
                data={
                    "run_id": run_id,
                    "status": "unknown",
                    "note": "runtime not connected",
                },
            )
        status = self.runtime_client.get_run_status(run_id)
        if status is None:
            return CommandResult(
                ok=True,
                text=f"Run {run_id}: not found",
                data={"run_id": run_id, "status": "not_found"},
            )
        return CommandResult(
            ok=True,
            text=f"Run {run_id}: {status.state} (agent={status.agent_id}, duration={status.duration_ms}ms, errors={status.error_count})",
            data={
                "run_id": run_id,
                "status": status.state,
                "agent_id": status.agent_id,
                "duration_ms": status.duration_ms,
                "error_count": status.error_count,
            },
        )

    def _cancel_run(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        if not command.args:
            return CommandResult(ok=False, text="Usage: /cancel <run_id>")
        run_id = command.args[0]
        if self.runtime_client is None:
            return CommandResult(
                ok=False,
                text="Runtime not connected",
                data={
                    "run_id": run_id,
                    "state": "error",
                    "note": "runtime not connected",
                },
            )
        success = self.runtime_client.cancel_run(run_id)
        if success:
            return CommandResult(
                ok=True,
                text=f"Cancel requested for run {run_id}.",
                data={"run_id": run_id, "state": "cancel_requested"},
            )
        return CommandResult(
            ok=False,
            text=f"Failed to cancel run {run_id}.",
            data={"run_id": run_id, "state": "cancel_failed"},
        )

    def _approve(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        if not command.args:
            return CommandResult(
                ok=False, text="Usage: /approve <request_id> [once|10m|1h|forever]"
            )
        request_id = command.args[0]
        grant = command.args[1] if len(command.args) > 1 else "once"

        # Map grant duration to action
        action_map = {
            "once": "allow_once",
            "10m": "allow_until",
            "1h": "allow_until",
            "forever": "allow_forever",
        }
        action = action_map.get(grant, "allow_once")
        until_seconds = None
        if grant == "10m":
            until_seconds = 600
        elif grant == "1h":
            until_seconds = 3600

        if self.policy_client is None:
            return CommandResult(
                ok=True,
                text=f"Approved {request_id} ({grant}) - policy service not connected",
                data={
                    "request_id": request_id,
                    "grant": grant,
                    "note": "policy not connected",
                },
            )

        success = self.policy_client.approve_request(request_id, action, until_seconds)
        if success:
            return CommandResult(
                ok=True,
                text=f"Approved {request_id} ({grant}).",
                data={"request_id": request_id, "grant": grant},
            )
        return CommandResult(
            ok=False,
            text=f"Failed to approve {request_id}.",
            data={"request_id": request_id, "error": "approval_failed"},
        )

    def _deny(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        if not command.args:
            return CommandResult(ok=False, text="Usage: /deny <request_id>")
        request_id = command.args[0]

        if self.policy_client is None:
            return CommandResult(
                ok=True,
                text=f"Denied {request_id} - policy service not connected",
                data={"request_id": request_id, "note": "policy not connected"},
            )

        success = self.policy_client.deny_request(request_id)
        if success:
            return CommandResult(
                ok=True, text=f"Denied {request_id}.", data={"request_id": request_id}
            )
        return CommandResult(
            ok=False,
            text=f"Failed to deny {request_id}.",
            data={"request_id": request_id, "error": "deny_failed"},
        )

    def _grants(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        if self.policy_client is None:
            return CommandResult(
                ok=True,
                text="No active grants.",
                data={"grants": [], "note": "policy not connected"},
            )
        grants = self.policy_client.list_grants(ctx.session_id)
        if not grants:
            return CommandResult(ok=True, text="No active grants.", data={"grants": []})
        lines = [
            f"  {g.get('grant_id', '?')}: {g.get('tool', '?')} ({g.get('duration_type', '?')})"
            for g in grants
        ]
        return CommandResult(
            ok=True, text="Active grants:\n" + "\n".join(lines), data={"grants": grants}
        )

    def _diag(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        return CommandResult(
            ok=True,
            text="controlplane: ok; dispatch=sync(default); durable inbox/outbox=enabled (sqlite backend).",
            data={"status": "ok"},
        )

    def _logs(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        if not command.args:
            return CommandResult(ok=False, text="Usage: /logs <run_id>")
        run_id = command.args[0]
        return CommandResult(
            ok=True,
            text=f"Logs for {run_id} are not wired yet.",
            data={"run_id": run_id, "lines": []},
        )

    def _artifact_ls(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        return CommandResult(
            ok=True,
            text="Artifact listing not wired (no artifactctl config).",
            data={"artifacts": []},
        )

    def _artifact_purge(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        return CommandResult(ok=True, text="Artifact purge triggered (stub).", data={})

    def _memory_ls(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        if self.memory_client is not None and hasattr(
            self.memory_client, "query_facts"
        ):
            limit = 10
            if command.args:
                try:
                    limit = max(1, int(command.args[0]))
                except (TypeError, ValueError):
                    limit = 10
            query = ""
            if len(command.args) > 1:
                query = " ".join(command.args[1:]).strip()
            try:
                rows = self.memory_client.query_facts(
                    session_id=ctx.session_id,
                    agent_id=ctx.agent_id,
                    query=query,
                    limit=limit,
                )
                lines: list[str] = []
                for row in rows:
                    text = str(getattr(row, "text", "") or "")
                    if not text and isinstance(row, dict):
                        text = str(row.get("text", "") or "")
                    if text:
                        lines.append(f"- {text}")
                return CommandResult(
                    ok=True,
                    text="Memory facts:\n" + ("\n".join(lines) if lines else "- none"),
                    data={
                        "candidates": [],
                        "facts": [
                            text[2:] if text.startswith("- ") else text
                            for text in lines
                        ],
                    },
                )
            except Exception as exc:
                return CommandResult(
                    ok=False,
                    text=f"Memory listing failed: {type(exc).__name__}: {str(exc)}",
                    error={"code": "MEMORY_QUERY_FAILED", "message": str(exc)},
                    data={"candidates": []},
                )
        return CommandResult(
            ok=True,
            text="Memory listing not wired (no memctl config).",
            data={"candidates": []},
        )

    def _memory_promote(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        if not command.args:
            return CommandResult(ok=False, text="Usage: /memory promote <memory_id>")
        if self.memory_client is not None and hasattr(
            self.memory_client, "promote_candidate"
        ):
            candidate_id = str(command.args[0]).strip()
            if not candidate_id:
                return CommandResult(
                    ok=False, text="Usage: /memory promote <memory_id>"
                )
            target_scope = (
                str(command.args[1]).strip()
                if len(command.args) > 1
                else f"agent:{ctx.agent_id}"
            )
            try:
                promoted = self.memory_client.promote_candidate(
                    candidate_id=candidate_id,
                    target_scope=target_scope,
                )
                promoted_id = str(getattr(promoted, "id", "") or "")
                return CommandResult(
                    ok=True,
                    text=f"Memory {candidate_id} promoted to {target_scope}.",
                    data={
                        "candidate_id": candidate_id,
                        "target_scope": target_scope,
                        "record_id": promoted_id,
                    },
                )
            except Exception as exc:
                return CommandResult(
                    ok=False,
                    text=f"Memory promotion failed: {type(exc).__name__}: {str(exc)}",
                    error={"code": "MEMORY_PROMOTE_FAILED", "message": str(exc)},
                    data={"candidate_id": candidate_id, "target_scope": target_scope},
                )
        return CommandResult(
            ok=True, text=f"Memory {command.args[0]} promoted (stub).", data={}
        )

    def _config_show(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        return CommandResult(ok=True, text="Config display not wired.", data={})

    def _config_set(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        if len(command.args) < 2:
            return CommandResult(ok=False, text="Usage: /config set <key> <value>")
        key, value = command.args[0], command.args[1]
        return CommandResult(
            ok=True,
            text=f"Config {key}={value} set (stub).",
            data={"key": key, "value": value},
        )

    def _skill_ingest(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """SMLF-07: Handle /skill ingest command in controlplane."""
        if not command.args:
            return CommandResult(
                ok=False,
                text="Usage: /skill ingest <path>\nExample: /skill ingest /path/to/SKILL.md",
            )
        path = command.args[0]
        try:
            from openminion.modules.skill import Skill
            from openminion.modules.skill.constants import DEFAULT_CONFIG_FILENAME
            from openminion.modules.skill.errors import SkillError
        except ModuleNotFoundError:
            return CommandResult(
                ok=False,
                text="Error: openminion-skill module not available.\nInstall with: pip install openminion-skill",
                error={
                    "code": "MODULE_NOT_FOUND",
                    "message": "openminion-skill not installed",
                },
            )
        try:
            ctl = Skill(DEFAULT_CONFIG_FILENAME)
            try:
                skill_id, version_hash, warnings = ctl.ingest_file(path=path)
                lines = [
                    "Successfully ingested skill",
                    f"  skill_id: {skill_id}",
                    f"  version_hash: {version_hash[:16]}...",
                ]
                if warnings:
                    lines.append(f"  warnings: {len(warnings)}")
                return CommandResult(
                    ok=True,
                    text="\n".join(lines),
                    data={
                        "skill_id": skill_id,
                        "version_hash": version_hash,
                        "warnings": warnings,
                    },
                )
            finally:
                ctl.close()
        except SkillError as exc:
            return CommandResult(
                ok=False,
                text=f"Error: {exc.code}\n  {exc.message}",
                error=exc.to_dict(),
            )
        except Exception as exc:
            return CommandResult(
                ok=False,
                text=f"Error: {type(exc).__name__}: {str(exc)}",
                error={"code": "UNKNOWN", "message": str(exc)},
            )

    def _skill_list(
        self, command: ParsedCommand, ctx: ResolvedContext
    ) -> CommandResult:
        """SMLF-07: Handle /skill list command in controlplane."""
        try:
            from openminion.modules.skill import Skill
            from openminion.modules.skill.constants import DEFAULT_CONFIG_FILENAME
            from openminion.modules.skill.errors import SkillError
        except ModuleNotFoundError:
            return CommandResult(
                ok=False,
                text="Error: openminion-skill module not available.\nInstall with: pip install openminion-skill",
                error={
                    "code": "MODULE_NOT_FOUND",
                    "message": "openminion-skill not installed",
                },
            )
        try:
            ctl = Skill(DEFAULT_CONFIG_FILENAME)
            try:
                skills = ctl.list_skills({})
                if not skills:
                    return CommandResult(
                        ok=True, text="No skills found.", data={"skills": []}
                    )
                lines = [f"Found {len(skills)} skill(s):"]
                for s in skills:
                    lines.append(
                        f"  {s['skill_id']} | {s.get('name', 'N/A')} | {s.get('status', 'N/A')}"
                    )
                return CommandResult(
                    ok=True, text="\n".join(lines), data={"skills": skills}
                )
            finally:
                ctl.close()
        except SkillError as exc:
            return CommandResult(
                ok=False,
                text=f"Error: {exc.code}\n  {exc.message}",
                error=exc.to_dict(),
            )
        except Exception as exc:
            return CommandResult(
                ok=False,
                text=f"Error: {type(exc).__name__}: {str(exc)}",
                error={"code": "UNKNOWN", "message": str(exc)},
            )

    def _modules(self, command: ParsedCommand, ctx: ResolvedContext) -> CommandResult:
        module_info = self.list_modules()
        lines = ["Module Diagnostics:"]

        if module_info["built_in"]:
            lines.append(f"\nBuilt-in ({len(module_info['built_in'])}):")
            for mod in module_info["built_in"]:
                lines.append(f"  - {mod} [system]")

        if module_info["loaded"]:
            lines.append(f"\nLoaded ({len(module_info['loaded'])}):")
            for mod in module_info["loaded"]:
                version = module_info["module_details"].get(mod, "unknown")
                lines.append(f"  - {mod}: v{version}")

        if module_info["shadowed"]:
            lines.append(f"\nShadowed ({len(module_info['shadowed'])}):")
            for mod in module_info["shadowed"]:
                lines.append(f"  - {mod} [shadowed by builtin]")

        if module_info["broken"]:
            lines.append(f"\nBroken ({len(module_info['broken'])}):")
            for mod in module_info["broken"]:
                error_info = module_info["errors"].get(mod, {})
                msg = error_info.get("error_message", "unknown error")
                timestamp = error_info.get("timestamp", "no timestamp")
                lines.append(f"  - {mod}: {msg} [at {timestamp}]")

        if not any(
            [
                module_info["built_in"],
                module_info["loaded"],
                module_info["shadowed"],
                module_info["broken"],
            ]
        ):
            lines.append("\nNo modules detected.")

        return CommandResult(ok=True, text="\n".join(lines), data=module_info)

    def _list_turns(self, session_id: str) -> list[object]:
        if hasattr(self.store, "list_turns"):
            return self.store.list_turns(session_id)
        return []

    def _emit_audit(self, event_type: str, **details: object) -> None:
        emit_audit_event(self.audit_logger, event_type, **details)
