# mypy: ignore-errors
from __future__ import annotations

from typing import Any, Dict, List, Optional

from openminion.base.logging import get_logger
from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime.audit import emit_audit_event

from .builtin_specs import COMMAND_HELP, SCOPE_DESCRIPTIONS, builtin_command_specs
from .module import AuthRequirement, CommandSpec

_LOGGER = get_logger("modules.controlplane.commands.registry")


class CommandRegistryBaseMixin:
    def _register_builtin_commands(self) -> None:
            """Register all built-in commands as CommandSpecs."""
            for spec in builtin_command_specs(self).values():
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
            lines = [
                "Available commands:",
                "Profile = runtime/model/tools config. Session = conversation context.",
                "Use /profile use <profile_id> to switch runtime profile; use /session new for fresh context.",
            ]
            for name, desc in sorted(COMMAND_HELP.items()):
                if "[admin]" in desc and not is_admin:
                    continue
                lines.append(f"  /{name} — {desc}")
            return CommandResult(
                ok=True, text="\n".join(lines), data={"is_admin": is_admin}
            )

    def _list_turns(self, session_id: str) -> list[object]:
            if hasattr(self.store, "list_turns"):
                return self.store.list_turns(session_id)
            return []

    def _current_channel_subject(
            self, ctx: ResolvedContext
        ) -> tuple[str | None, str | None]:
            raw = str(ctx.chat_key or "").strip()
            if ":" not in raw:
                return None, None
            channel, subject_id = raw.split(":", 1)
            channel = channel.strip()
            subject_id = subject_id.strip()
            if not channel or not subject_id:
                return None, None
            return channel, subject_id

    def _current_pairing(self, ctx: ResolvedContext) -> dict[str, Any] | None:
            channel, subject_id = self._current_channel_subject(ctx)
            if channel is None or subject_id is None:
                return None
            get_pairing = getattr(self.store, "get_pairing", None)
            if not callable(get_pairing):
                return None
            pairing = get_pairing(channel=channel, chat_id=subject_id)
            return dict(pairing) if isinstance(pairing, dict) else None

    def _describe_scopes(self, scopes: object) -> str:
            if not isinstance(scopes, (list, tuple, set)):
                return "none"
            rendered: list[str] = []
            for scope in scopes:
                raw = str(scope or "").strip()
                if not raw:
                    continue
                label = SCOPE_DESCRIPTIONS.get(raw, raw)
                rendered.append(f"{label} ({raw})" if label != raw else raw)
            return ", ".join(rendered) if rendered else "none"

    def _emit_audit(self, event_type: str, **details: object) -> None:
            emit_audit_event(self.audit_logger, event_type, **details)
