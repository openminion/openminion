from __future__ import annotations

from openminion.base.version import OPENMINION_VERSION

from .module import AuthRequirement, CommandSchema, CommandSpec, Handler

COMMAND_HELP: dict[str, str] = {
    "help": "Show available commands",
    "status": "Show Telegram/controlplane connection, session, and profile status",
    "new": "Start a fresh session",
    "sessions": "List sessions for this chat/user",
    "session.use": "Rebind this chat to an existing session: /session use <session_id>",
    "session.title": "Set current session title: /session title <text>",
    "export": "Export current session: /export [md|json]",
    "profile": "Show current runtime profile",
    "profile.current": "Show current runtime profile",
    "profile.list": "List configured runtime profiles",
    "profile.ls": "List configured runtime profiles",
    "profile.use": "Switch this session to a runtime profile: /profile use <profile_id>",
    "profile.info": "Show details for a runtime profile: /profile info <profile_id>",
    "profile.stop": "Stop current profile session",
    "agent": "Compatibility alias for /profile",
    "agent.list": "Compatibility alias for /profile list",
    "agent.set": "Compatibility alias for /profile use",
    "agent.ls": "Compatibility alias for /profile list",
    "agent.use": "Compatibility alias for /profile use",
    "agent.info": "Compatibility alias for /profile info",
    "agent.stop": "Compatibility alias for /profile stop",
    "pair": "Show pairing status for this chat",
    "pair.status": "Show pairing status for this chat",
    "pair.revoke": "Revoke pairing for this chat",
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

SCOPE_DESCRIPTIONS: dict[str, str] = {
    "chat.interact": "chat with OpenMinion",
    "cp.message.read": "read controlplane messages",
    "cp.message.write": "send controlplane messages",
    "session.read": "read session state",
    "session.write": "change session state",
    "run.start": "start runs",
    "tool.weather.read": "use weather tools",
    "tool.search.read": "use search tools",
}

_HANDLER_METHODS: dict[str, str] = {
    "help": "_help",
    "status": "_status",
    "new": "_session_new",
    "sessions": "_sessions",
    "session.use": "_session_use",
    "session.title": "_session_title",
    "export": "_export",
    "profile": "_agent_show",
    "profile.current": "_agent_show",
    "profile.list": "_agent_ls",
    "profile.ls": "_agent_ls",
    "profile.use": "_agent_use",
    "profile.info": "_agent_info",
    "profile.stop": "_agent_stop",
    "agent": "_agent_show",
    "agent.list": "_agent_ls",
    "agent.set": "_agent_use",
    "agent.ls": "_agent_ls",
    "agent.use": "_agent_use",
    "agent.info": "_agent_info",
    "agent.stop": "_agent_stop",
    "pair": "_pair_status",
    "pair.status": "_pair_status",
    "pair.revoke": "_pair_revoke",
    "session.new": "_session_new",
    "session.id": "_session_id",
    "session.status": "_session_status",
    "run": "_run_show",
    "run.status": "_run_status",
    "cancel": "_cancel_run",
    "job.ls": "_job_ls",
    "approve": "_approve",
    "deny": "_deny",
    "grants": "_grants",
    "diag": "_diag",
    "logs": "_logs",
    "artifact.ls": "_artifact_ls",
    "artifact.purge": "_artifact_purge",
    "memory.ls": "_memory_ls",
    "memory.promote": "_memory_promote",
    "config.show": "_config_show",
    "config.set": "_config_set",
    "skill.ingest": "_skill_ingest",
    "skill.list": "_skill_list",
    "modules": "_modules",
}


def builtin_command_specs(registry: object) -> dict[str, CommandSpec]:
    specs: dict[str, CommandSpec] = {}
    for command_name, method_name in _HANDLER_METHODS.items():
        handler = getattr(registry, method_name)
        specs[command_name] = _command_spec(command_name, handler)
    return specs


def _command_spec(command_name: str, handler: Handler) -> CommandSpec:
    help_text = COMMAND_HELP.get(command_name, f"Built-in command: {command_name}")
    schema = CommandSchema(
        name=command_name,
        description=help_text,
        usage=f"/{command_name} [args...]",
    )
    auth_requirement = (
        AuthRequirement.ADMIN if "[admin]" in help_text else AuthRequirement.USER
    )
    return CommandSpec(
        name=command_name,
        schema=schema,
        handler=handler,
        auth_requirement=auth_requirement,
        module_name="builtin",
        version=OPENMINION_VERSION,
    )
