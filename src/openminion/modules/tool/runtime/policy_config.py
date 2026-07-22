# mypy: ignore-errors
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, cast

from ..constants import (
    TOOL_DANGEROUS_MODE_ALLOW,
    TOOL_DANGEROUS_MODE_DENY,
    TOOL_DANGEROUS_MODE_PROMPT,
    TOOL_EXEC_ASK_ALWAYS,
    TOOL_EXEC_ASK_OFF,
    TOOL_EXEC_ASK_ON_MISS,
    TOOL_EXEC_SECURITY_ALLOWLIST,
    TOOL_EXEC_SECURITY_DENY,
    TOOL_EXEC_SECURITY_FULL,
    TOOL_REDACTION_MODE_NORMAL,
    TOOL_REDACTION_MODE_OFF,
    TOOL_REDACTION_MODE_STRICT,
)
from ..contracts.schemas import Scope
from ..errors import ToolRuntimeError
from .policy_shared import SCOPE_ORDER, _invalid_argument


class PolicyConfigMixin:
    def max_scope(self) -> Scope:
        raw_scope = self.raw.get("scope", "WRITE_SAFE")
        if raw_scope not in SCOPE_ORDER:
            raise _invalid_argument(f"Invalid policy scope: {raw_scope}")
        return cast(Scope, raw_scope)

    def effective_scope(self, requested: Optional[str]) -> Scope:
        policy_max = self.max_scope()
        if not requested:
            return policy_max
        if requested not in SCOPE_ORDER:
            raise _invalid_argument(f"Unknown requested scope: {requested}")
        req = cast(Scope, requested)
        return req if SCOPE_ORDER[req] <= SCOPE_ORDER[policy_max] else policy_max

    def limits(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.raw.get("limits", {}))

    def limit_int(self, key: str, default: int) -> int:
        val = self.limits().get(key, default)
        try:
            return int(val)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"Invalid policy limit for {key}: {val}"
            ) from exc

    def redaction_mode(self) -> str:
        mode = self.raw.get("redaction", {}).get("mode", TOOL_REDACTION_MODE_NORMAL)
        if mode not in (
            TOOL_REDACTION_MODE_NORMAL,
            TOOL_REDACTION_MODE_STRICT,
            TOOL_REDACTION_MODE_OFF,
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"Invalid redaction mode: {mode}"
            )
        return str(mode)

    def exec_config(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.raw.get("exec", {}))

    def dangerous_config(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.raw.get("dangerous", {}))

    def exec_security_mode(self) -> str:
        mode = (
            str(
                self.exec_config().get("security", TOOL_EXEC_SECURITY_ALLOWLIST)
                or TOOL_EXEC_SECURITY_ALLOWLIST
            )
            .strip()
            .lower()
        )
        if mode not in {
            TOOL_EXEC_SECURITY_DENY,
            TOOL_EXEC_SECURITY_ALLOWLIST,
            TOOL_EXEC_SECURITY_FULL,
        }:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"Invalid exec security mode: {mode}"
            )
        return mode

    def exec_ask_mode(self) -> str:
        mode = (
            str(
                self.exec_config().get("ask", TOOL_EXEC_ASK_ON_MISS)
                or TOOL_EXEC_ASK_ON_MISS
            )
            .strip()
            .lower()
        )
        if mode not in {
            TOOL_EXEC_ASK_OFF,
            TOOL_EXEC_ASK_ON_MISS,
            TOOL_EXEC_ASK_ALWAYS,
        }:
            raise ToolRuntimeError("INVALID_ARGUMENT", f"Invalid exec ask mode: {mode}")
        return mode

    def exec_ask_fallback(self) -> str:
        mode = (
            str(
                self.exec_config().get("askFallback", TOOL_EXEC_SECURITY_DENY)
                or TOOL_EXEC_SECURITY_DENY
            )
            .strip()
            .lower()
        )
        if mode not in {
            TOOL_EXEC_SECURITY_DENY,
            TOOL_EXEC_SECURITY_ALLOWLIST,
            TOOL_EXEC_SECURITY_FULL,
        }:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"Invalid exec askFallback mode: {mode}"
            )
        return mode

    def exec_allowlist(self) -> list[str]:
        raw = self.exec_config().get("allowlist", [])
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if str(item).strip()]

    def dangerous_enabled(self) -> bool:
        return bool(self.dangerous_config().get("enabled", True))

    def dangerous_mode(self) -> str:
        mode = (
            str(
                self.dangerous_config().get("mode", TOOL_DANGEROUS_MODE_PROMPT)
                or TOOL_DANGEROUS_MODE_PROMPT
            )
            .strip()
            .lower()
        )
        if mode not in {
            TOOL_DANGEROUS_MODE_PROMPT,
            TOOL_DANGEROUS_MODE_DENY,
            TOOL_DANGEROUS_MODE_ALLOW,
        }:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"Invalid dangerous mode: {mode}"
            )
        return mode

    def workspace_root(self) -> Path:
        value = str(self.raw.get("workspace_root", "~/openminion_tool_runs"))
        return Path(value).expanduser().resolve(strict=False)
