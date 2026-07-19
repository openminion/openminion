# mypy: ignore-errors
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, cast

from openminion.base.config.env import resolve_environment_config

from ..constants import (
    TOOL_DANGEROUS_MODE_ALLOW,
    TOOL_DANGEROUS_MODE_DENY,
    TOOL_EXEC_ASK_ALWAYS,
    TOOL_EXEC_ASK_ON_MISS,
    TOOL_EXEC_SECURITY_ALLOWLIST,
    TOOL_EXEC_SECURITY_DENY,
)
from ..contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from ..errors import ToolRuntimeError
from .command_patterns import command_action_class, effective_command_argv, matching_allow_pattern
from .policy_shared import _MKDIR_SCAFFOLD_HINT, _invalid_argument


class PolicyExecMixin:
    @staticmethod
    def _normalize_exec_name(exec_name: str) -> str:
        return (
            os.path.basename(exec_name)
            if ("/" in exec_name or "\\" in exec_name)
            else exec_name
        )

    def ensure_exec_allowed(
        self, *, argv: list[str], workspace: Path, confirm: bool
    ) -> str:
        effective_argv = effective_command_argv(argv)
        if not effective_argv:
            raise _invalid_argument("cmd.run argv must include executable")
        raw_exec = str(effective_argv[0])
        exec_name = self._normalize_exec_name(raw_exec)
        if not exec_name:
            raise _invalid_argument("cmd.run executable cannot be empty")

        security_mode = self.exec_security_mode()
        ask_mode = self.exec_ask_mode()
        allowlist = self.exec_allowlist()
        commands = cast(Dict[str, Any], self.raw.get("commands", {}))
        allow_pattern = matching_allow_pattern(effective_argv, commands)

        def _ask_required(rule: str, details: Dict[str, Any]) -> None:
            if confirm:
                return
            raise ToolRuntimeError(
                TOOL_ERROR_CONFIRM_REQUIRED,
                "Exec approval required",
                {"rule": rule, "exec": exec_name, **details},
            )

        if security_mode == TOOL_EXEC_SECURITY_DENY:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Denied by policy: exec is disabled for '{exec_name}'",
                {"rule": "exec.security", "mode": security_mode},
            )

        if security_mode == TOOL_EXEC_SECURITY_ALLOWLIST:
            allowed = allow_pattern is not None
            for item in allowlist:
                token = str(item)
                if not token:
                    continue
                if token == exec_name or token == raw_exec:
                    allowed = True
                    break
            if not allowed:
                if ask_mode in {TOOL_EXEC_ASK_ON_MISS, TOOL_EXEC_ASK_ALWAYS}:
                    _ask_required("exec.ask.on_miss", {"mode": ask_mode})
                    allowed = True
                else:
                    raise ToolRuntimeError(
                        "POLICY_DENIED",
                        f"Denied by policy: command '{exec_name}' is not allowlisted",
                        {"rule": "exec.allowlist"},
                    )

        if ask_mode == TOOL_EXEC_ASK_ALWAYS:
            _ask_required("exec.ask.always", {"mode": ask_mode})

        return exec_name

    def ensure_dangerous_allowed(
        self,
        *,
        dangerous: bool,
        pattern_id: str | None,
        reason: str | None,
        confirm: bool,
    ) -> None:
        if not self.dangerous_enabled() or not dangerous:
            return
        mode = self.dangerous_mode()
        details = {
            "rule": "dangerous",
            "pattern_id": pattern_id or "",
            "reason": reason or "",
        }
        if mode == TOOL_DANGEROUS_MODE_ALLOW:
            return
        if mode == TOOL_DANGEROUS_MODE_DENY:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                "Denied by policy: dangerous command blocked",
                details,
            )
        if not confirm:
            raise ToolRuntimeError(
                TOOL_ERROR_CONFIRM_REQUIRED,
                "Approval required for dangerous command",
                details,
            )

    def ensure_command_allowed(self, argv: list[str]) -> str:
        effective_argv = effective_command_argv(argv)
        if not effective_argv:
            raise _invalid_argument("cmd.run argv must include executable")
        raw_exec = effective_argv[0]
        exec_name = (
            os.path.basename(raw_exec)
            if ("/" in raw_exec or "\\" in raw_exec)
            else raw_exec
        )
        exec_name = exec_name.strip()
        if not exec_name:
            raise _invalid_argument("cmd.run executable cannot be empty")

        commands = cast(Dict[str, Any], self.raw.get("commands", {}))
        deny_exact = set(commands.get("deny_exact", []))
        if exec_name in deny_exact:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Denied by policy: command '{exec_name}' is denylisted",
                {"rule": "commands.deny_exact", "command": exec_name},
            )

        for expr in commands.get("deny_regex", []):
            if re.search(str(expr), " ".join(argv)):
                raise ToolRuntimeError(
                    "POLICY_DENIED",
                    f"Denied by policy: command '{exec_name}' matched deny regex",
                    {"rule": "commands.deny_regex", "regex": expr},
                )

        mode_raw = str(commands.get("mode", TOOL_EXEC_SECURITY_ALLOWLIST))
        mode = mode_raw.lower()
        allow = set(commands.get("allow", []))
        allow_pattern = matching_allow_pattern(effective_argv, commands)
        action_class = command_action_class(effective_argv)

        if mode == TOOL_EXEC_SECURITY_ALLOWLIST and action_class == "install":
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Denied by policy: command '{exec_name}' is an install command",
                {
                    "rule": "commands.install",
                    "command": exec_name,
                    "action_class": action_class,
                },
            )

        if mode == TOOL_EXEC_SECURITY_ALLOWLIST:
            if exec_name not in allow and allow_pattern is None:
                raise ToolRuntimeError(
                    "POLICY_DENIED",
                    f"Denied by policy: command '{exec_name}' is not allowlisted",
                    {
                        "rule": "commands.allow",
                        "command": exec_name,
                        "action_class": action_class,
                        **(_MKDIR_SCAFFOLD_HINT if exec_name == "mkdir" else {}),
                    },
                )
        elif mode == "blocklist":
            pass
        else:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Invalid command mode: {mode_raw}",
                {"rule": "commands.mode"},
            )

        return exec_name

    def filter_env(self, raw_env: Dict[str, str]) -> Dict[str, str]:
        env_cfg = cast(Dict[str, Any], self.raw.get("env", {}))
        allow_keys = set(env_cfg.get("allow_keys", []))
        deny_regex = [
            re.compile(str(expr)) for expr in env_cfg.get("deny_keys_regex", [])
        ]
        process_env = resolve_environment_config().snapshot()

        out: Dict[str, str] = {}

        for key in allow_keys:
            if key in process_env:
                out[key] = process_env[key]

        for key, value in raw_env.items():
            if allow_keys and key not in allow_keys:
                continue
            if any(expr.search(key) for expr in deny_regex):
                continue
            out[key] = value

        return out
