# mypy: ignore-errors
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, cast

from ..contracts.schemas import Scope, TOOL_ERROR_CONFIRM_REQUIRED
from ..errors import ToolRuntimeError
from .policy_normalization import canonical_tool_name
from .policy_paths import _expand_path_pair, _is_subpath, _resolve_candidate_path
from .policy_shared import SCOPE_ORDER


class PolicyAccessMixin:
    def is_plugin_enabled(self, name: str) -> bool:
        plugins = cast(Dict[str, Any], self.raw.get("plugins", {}))
        deny = set(plugins.get("deny", []))
        if name in deny:
            return False
        allow = list(plugins.get("allow", []))
        if allow:
            return name in allow
        return True

    def ensure_tool_allowed(self, tool_name: str) -> None:
        tools = cast(Dict[str, Any], self.raw.get("tools", {}))
        deny_exact = set(tools.get("deny_exact", []))
        if tool_name in deny_exact:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Denied by policy: tool '{tool_name}' is denylisted",
                {"rule": "tools.deny_exact"},
            )
        for pref in tools.get("deny_prefix", []):
            if tool_name.startswith(pref):
                raise ToolRuntimeError(
                    "POLICY_DENIED",
                    f"Denied by policy: tool '{tool_name}' is denied by prefix",
                    {"rule": "tools.deny_prefix", "prefix": pref},
                )
        allow_prefix = list(tools.get("allow_prefix", []))
        if allow_prefix and not any(
            tool_name.startswith(pref) for pref in allow_prefix
        ):
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Denied by policy: tool '{tool_name}' is outside allowed prefixes",
                {"rule": "tools.allow_prefix"},
            )

    def ensure_scope_allowed(
        self, effective_scope: Scope, tool_min_scope: Scope, tool_name: str
    ) -> None:
        if SCOPE_ORDER[effective_scope] < SCOPE_ORDER[tool_min_scope]:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                f"Denied by policy: tool '{tool_name}' requires scope '{tool_min_scope}'",
                {
                    "rule": "scope.minimum",
                    "effective_scope": effective_scope,
                    "required_scope": tool_min_scope,
                },
            )

    def ensure_confirm_if_required(
        self,
        tool_name: str,
        args: Dict[str, Any],
        confirm: bool,
        dangerous_default: bool,
    ) -> None:
        tool_name_canonical = canonical_tool_name(tool_name)
        required = False
        reason: Dict[str, Any] = {}
        confirm_cfg = cast(Dict[str, Any], self.raw.get("confirm", {}))

        required_tools = {
            canonical_tool_name(str(name))
            for name in confirm_cfg.get("required_tools", [])
        }
        if tool_name_canonical in required_tools or dangerous_default:
            required = True
            reason = {"rule": "confirm.required_tools", "tool": tool_name_canonical}

        for rule in confirm_cfg.get("required_when", []):
            if not isinstance(rule, dict):
                continue
            rule_tool = canonical_tool_name(str(rule.get("tool", "")))
            if rule_tool != tool_name_canonical:
                continue
            args_match = cast(Dict[str, Any], rule.get("args_match", {}))
            if args_match and all(
                args.get(key) == val for key, val in args_match.items()
            ):
                required = True
                reason = {
                    "rule": "confirm.required_when.args_match",
                    "match": args_match,
                }
            argv_tokens = list(rule.get("args_match_contains_argv", []))
            if argv_tokens:
                argv = [str(x) for x in cast(Iterable[Any], args.get("argv", []))]
                argv_text = " ".join(argv)
                if all(token in argv_text for token in argv_tokens):
                    required = True
                    reason = {
                        "rule": "confirm.required_when.args_match_contains_argv",
                        "tokens": argv_tokens,
                    }

        if required and not confirm:
            raise ToolRuntimeError(
                TOOL_ERROR_CONFIRM_REQUIRED,
                "Denied by policy: operation requires explicit confirmation",
                {**reason, "suggestion": "Retry with meta.confirm=true or --confirm"},
            )

    def ensure_path_allowed(
        self, raw_path: str, workspace: Path, operation: str
    ) -> Path:
        if operation not in ("read", "write"):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", f"Unsupported path operation: {operation}"
            )

        candidate_path, resolved_path = _resolve_candidate_path(raw_path, workspace)

        paths = cast(Dict[str, Any], self.raw.get("paths", {}))
        deny_roots = [
            _expand_path_pair(str(p), workspace) for p in paths.get("deny", [])
        ]
        allow_key = "read_allow" if operation == "read" else "write_allow"
        allow_roots = [
            _expand_path_pair(str(p), workspace) for p in paths.get(allow_key, [])
        ]

        for denied_candidate, denied_resolved in deny_roots:
            if (
                _is_subpath(candidate_path, denied_candidate)
                or _is_subpath(candidate_path, denied_resolved)
                or _is_subpath(resolved_path, denied_resolved)
            ):
                raise ToolRuntimeError(
                    "POLICY_DENIED",
                    f"Denied by policy: path '{resolved_path}' is in deny roots",
                    {
                        "rule": "paths.deny",
                        "path": str(resolved_path),
                        "deny_root": str(denied_resolved),
                    },
                )

        for allowed_candidate, allowed_resolved in allow_roots:
            # Accept either lexical alias form or canonical resolved form for
            candidate_allowed = _is_subpath(
                candidate_path, allowed_candidate
            ) or _is_subpath(candidate_path, allowed_resolved)
            resolved_allowed = _is_subpath(resolved_path, allowed_resolved)

            if candidate_allowed and resolved_allowed:
                return resolved_path

            if candidate_allowed and not resolved_allowed:
                raise ToolRuntimeError(
                    "POLICY_DENIED",
                    "Denied by policy: resolved path escapes allowed root",
                    {
                        "rule": "paths.allow.symlink_escape",
                        "requested_path": str(candidate_path),
                        "resolved_path": str(resolved_path),
                        "allow_root": str(allowed_resolved),
                    },
                )

        raise ToolRuntimeError(
            "POLICY_DENIED",
            f"Denied by policy: path '{resolved_path}' is outside allowed roots",
            {"rule": f"paths.{allow_key}", "path": str(resolved_path)},
        )
