import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, cast

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for minimal environments

    class _YamlFallback:
        @staticmethod
        def safe_load(raw: str) -> Any:
            import json

            try:
                return json.loads(raw)
            except Exception:
                return {}

    yaml = _YamlFallback()  # type: ignore[assignment]

from ..errors import ToolRuntimeError
from ..contracts.schemas import Scope, TOOL_ERROR_CONFIRM_REQUIRED
from openminion.base.config.env import resolve_environment_config
from ..constants import (
    TOOL_AUDIT_WRITE_MODE_DUAL,
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
from .policy_normalization import (
    canonical_tool_name,
    dedupe as _dedupe,
    dedupe_normalized as _dedupe_normalized,
    deep_merge as _deep_merge,
    normalize_policy_legacy_aliases as _normalize_policy_legacy_aliases,
)
from .command_patterns import (
    COMMAND_ALLOW_PATTERNS,
    DISCOVERY_KNOWN_TOOLS,
    command_action_class,
    effective_command_argv,
    matching_allow_pattern,
)

SCOPE_ORDER = {"READ_ONLY": 0, "WRITE_SAFE": 1, "POWER_USER": 2, "UI_AUTOMATION": 3}
_MKDIR_SCAFFOLD_HINT = {
    "suggested_tool": "file.write",
    "suggested_fix": (
        "For project scaffolding, write the target file directly with file.write; "
        "parent directories are created automatically by default."
    ),
}


def _invalid_argument(message: str) -> ToolRuntimeError:
    return ToolRuntimeError("INVALID_ARGUMENT", message)


def reorder_runtime_chain(
    *,
    runtime_binding_id: str,
    default_chain: Iterable[str],
    runtime_binding_policies: Dict[str, Any] | None,
    available_tool_names: Iterable[str] | None = None,
) -> tuple[str, ...]:
    manager = ToolBindingPolicyManager.from_runtime_binding_policy_payload(
        runtime_binding_policies
    )
    return manager.reorder_runtime_chain(
        runtime_binding_id=runtime_binding_id,
        default_chain=tuple(default_chain),
        available_tool_names=tuple(available_tool_names)
        if available_tool_names is not None
        else None,
    )


@dataclass(frozen=True)
class RuntimeBindingPolicy:
    runtime_binding_id: str
    primary: str
    fallback_tools: tuple[str, ...]


class ToolBindingPolicyManager:
    def __init__(
        self,
        *,
        policies: Mapping[str, RuntimeBindingPolicy] | None = None,
        selection_strategy: str = "ordered",
        fallback_on: Sequence[str] = (),
        no_fallback_on: Sequence[str] = (),
    ) -> None:
        self._policies = dict(policies or {})
        self._selection_strategy = (
            str(selection_strategy or "ordered").strip() or "ordered"
        )
        self._fallback_on = tuple(_dedupe_normalized(fallback_on))
        self._no_fallback_on = tuple(_dedupe_normalized(no_fallback_on))

    @classmethod
    def from_tool_selection_config(cls, config: Any) -> "ToolBindingPolicyManager":
        return cls.from_tool_selection_config_with_defaults(config)

    @classmethod
    def from_tool_selection_config_with_defaults(
        cls,
        config: Any,
        *,
        default_policies: Mapping[str, RuntimeBindingPolicy] | None = None,
    ) -> "ToolBindingPolicyManager":
        runtime_bindings = getattr(config, "runtime_bindings", {}) or {}
        parsed: dict[str, RuntimeBindingPolicy] = dict(default_policies or {})
        for runtime_binding_id, binding in runtime_bindings.items():
            binding_id = str(runtime_binding_id or "").strip()
            if not binding_id:
                continue
            primary = str(getattr(binding, "primary", "") or "").strip()
            fallback_tools = [
                str(item).strip()
                for item in (getattr(binding, "fallback_tools", []) or [])
                if str(item).strip()
            ]
            parsed[binding_id] = RuntimeBindingPolicy(
                runtime_binding_id=binding_id,
                primary=primary,
                fallback_tools=tuple(_dedupe(fallback_tools)),
            )
        return cls(
            policies=parsed,
            selection_strategy=str(
                getattr(config, "runtime_binding_selection_strategy", "ordered")
                or "ordered"
            ),
            fallback_on=getattr(config, "runtime_fallback_on", ()) or (),
            no_fallback_on=getattr(config, "runtime_no_fallback_on", ()) or (),
        )

    @staticmethod
    def default_policy(
        runtime_binding_id: str, candidates: Sequence[str]
    ) -> RuntimeBindingPolicy | None:
        binding_id = str(runtime_binding_id or "").strip()
        ordered = tuple(_dedupe(candidates))
        if not binding_id or not ordered:
            return None
        return RuntimeBindingPolicy(
            runtime_binding_id=binding_id,
            primary=ordered[0],
            fallback_tools=ordered[1:],
        )

    @classmethod
    def from_runtime_binding_policy_payload(
        cls,
        payload: Mapping[str, Any] | None,
    ) -> "ToolBindingPolicyManager":
        parsed: dict[str, RuntimeBindingPolicy] = {}
        source = payload or {}
        policies_raw = source.get("runtime_binding_policies")
        if not isinstance(policies_raw, Mapping):
            policies_raw = source

        for runtime_binding_id, raw_policy in policies_raw.items():
            binding_id = str(runtime_binding_id or "").strip()
            if not binding_id:
                continue
            if isinstance(raw_policy, RuntimeBindingPolicy):
                parsed[binding_id] = raw_policy
                continue
            if not isinstance(raw_policy, Mapping):
                continue

            primary = str(raw_policy.get("primary", "") or "").strip()
            fallback_raw = raw_policy.get("fallback_tools", ())
            if isinstance(fallback_raw, str):
                fallback_items = [
                    item.strip() for item in fallback_raw.split(",") if item.strip()
                ]
            elif isinstance(fallback_raw, Sequence):
                fallback_items = [
                    str(item).strip() for item in fallback_raw if str(item).strip()
                ]
            else:
                fallback_items = []

            parsed[binding_id] = RuntimeBindingPolicy(
                runtime_binding_id=binding_id,
                primary=primary,
                fallback_tools=tuple(_dedupe(fallback_items)),
            )

        selection_strategy = (
            str(
                source.get("runtime_binding_selection_strategy", "ordered") or "ordered"
            ).strip()
            or "ordered"
        )
        fallback_on = source.get("runtime_fallback_on", ())
        no_fallback_on = source.get("runtime_no_fallback_on", ())
        return cls(
            policies=parsed,
            selection_strategy=selection_strategy,
            fallback_on=fallback_on if isinstance(fallback_on, Sequence) else (),
            no_fallback_on=no_fallback_on
            if isinstance(no_fallback_on, Sequence)
            else (),
        )

    def policy_for(self, runtime_binding_id: str) -> RuntimeBindingPolicy | None:
        binding_id = str(runtime_binding_id or "").strip()
        if not binding_id:
            return None
        return self._policies.get(binding_id)

    def runtime_binding_policies_payload(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for binding_id in sorted(self._policies.keys()):
            policy = self._policies[binding_id]
            out[binding_id] = {
                "primary": policy.primary,
                "fallback_tools": list(policy.fallback_tools),
            }
        return out

    def metadata_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        policies = self.runtime_binding_policies_payload()
        if policies:
            payload["runtime_binding_policies"] = policies
        if self._selection_strategy:
            payload["runtime_binding_selection_strategy"] = self._selection_strategy
        if self._fallback_on:
            payload["runtime_fallback_on"] = list(self._fallback_on)
        if self._no_fallback_on:
            payload["runtime_no_fallback_on"] = list(self._no_fallback_on)
        return payload

    def reorder_runtime_chain(
        self,
        *,
        runtime_binding_id: str,
        default_chain: Sequence[str],
        available_tool_names: Sequence[str] | None = None,
    ) -> tuple[str, ...]:
        binding_id = str(runtime_binding_id or "").strip()
        if not binding_id:
            return tuple(_dedupe(default_chain))

        available: set[str] | None = None
        default_items = [
            str(item).strip() for item in default_chain if str(item).strip()
        ]
        if available_tool_names is not None:
            available = {
                str(item).strip() for item in available_tool_names if str(item).strip()
            }
            default_items = [item for item in default_items if item in available]

        policy = self._policies.get(binding_id)
        if policy is None:
            return tuple(_dedupe(default_items))

        preferred = [policy.primary, *policy.fallback_tools]
        ordered: list[str] = []
        seen: set[str] = set()

        for candidate in preferred:
            token = str(candidate or "").strip()
            if not token or token in seen:
                continue
            if available is not None and token not in available:
                continue
            ordered.append(token)
            seen.add(token)

        for candidate in default_items:
            if candidate in seen:
                continue
            ordered.append(candidate)
            seen.add(candidate)
        return tuple(ordered)

    def should_fallback(self, *, error_text: str) -> bool:
        text = str(error_text or "").strip().lower()
        if not text:
            return False
        if self._no_fallback_on and any(
            token in text for token in self._no_fallback_on
        ):
            return False
        if self._fallback_on and any(token in text for token in self._fallback_on):
            return True
        return False


DEFAULT_POLICY: Dict[str, Any] = {
    "version": 1,
    "scope": "WRITE_SAFE",
    "workspace_root": "~/openminion_tool_runs",
    "plugins": {
        "allow": [
            "openminion_tool",
            "openminion_tool_browser",
            "openminion_tool_browser_pinchtab",
            "openminion_tool_search_brave",
            "openminion_tool_exec",
            "openminion_tool_reactions",
            "openminion_tool_weather_openmeteo",
            "openminion_tool_gws",
            "openminion_tool_time",
            "openminion_tool_host",
        ],
        "deny": [],
    },
    "tools": {
        "allow_prefix": [
            "file.",
            "code.",
            "cmd.",
            "sys.",
            "proc.",
            "tool.",
            "browser",
            "web.",
            "exec.",
            "git.",
            "plan.",
            "reactions.",
            "weather",
            "time",
            "location",
            "host.",
            "ip.",
            "gws.",
            "fetch.",
            "task.",
            "skill.",
            "mcp.",
        ],
        "deny_exact": [],
        "deny_prefix": [],
        "weather_openmeteo": {
            "fallback": {
                "enabled": True,
            }
        },
    },
    "paths": {
        "read_allow": ["${WORKSPACE}", "~/projects", "~/Downloads"],
        "write_allow": ["${WORKSPACE}"],
        "deny": [
            "/etc",
            "/System",
            "/Library/Keychains",
            "~/.ssh",
            "~/.gnupg",
            "C:\\Windows",
            "C:\\Program Files",
            "C:\\Program Files (x86)",
        ],
    },
    "commands": {
        "mode": TOOL_EXEC_SECURITY_ALLOWLIST,
        "allow": [
            "git",
            "python",
            "python3",
            "python3.11",
            "node",
            "npm",
            "yarn",
            "make",
            "bash",
            "zsh",
            "sh",
            "ls",
            "pwd",
            "echo",
            "cat",
            "head",
            "tail",
            "grep",
            "rg",
            "ripgrep",
            "sed",
            "awk",
            "cut",
            "sort",
            "uniq",
            "wc",
        ],
        "deny_exact": ["rm", "dd", "mkfs"],
        "deny_regex": [".*shutdown.*", ".*reboot.*", ".*poweroff.*", ".*halt.*"],
        "known_tools": list(DISCOVERY_KNOWN_TOOLS),
        "allow_patterns": list(COMMAND_ALLOW_PATTERNS),
    },
    "exec": {
        "security": TOOL_EXEC_SECURITY_ALLOWLIST,
        "ask": TOOL_EXEC_ASK_ON_MISS,
        "askFallback": TOOL_EXEC_SECURITY_DENY,
        "allowlist": [],
    },
    "dangerous": {
        "enabled": True,
        "mode": TOOL_DANGEROUS_MODE_PROMPT,
        "approvals": {
            "allow_once": True,
            "allow_session": True,
            "allow_always": True,
            "deny_default": True,
        },
    },
    "audit": {
        "write_mode": TOOL_AUDIT_WRITE_MODE_DUAL,
        "retention_days": 30,
        "gc_on_startup": False,
    },
    "env": {
        "allow_keys": ["PATH", "PYTHONPATH", "NODE_ENV"],
        "deny_keys_regex": [".*KEY.*", ".*TOKEN.*", ".*SECRET.*"],
    },
    "confirm": {
        "required_tools": ["file.delete", "proc.kill"],
        "required_when": [
            {"tool": "file.delete", "args_match": {"recursive": True}},
            {"tool": "file.copy", "args_match": {"overwrite": True}},
            {"tool": "file.move", "args_match": {"overwrite": True}},
            {"tool": "cmd.run", "args_match_contains_argv": ["sudo"]},
        ],
    },
    "limits": {
        "outer_timeout_sec": 60,
        "cmd_timeout_sec": 45,
        "cmd_max_output_bytes": 200000,
        "file_max_read_bytes": 200000,
        "fs_list_max_entries": 500,
        "max_artifact_bytes_total": 50000000,
        "max_single_artifact_bytes": 10000000,
    },
    "redaction": {"mode": TOOL_REDACTION_MODE_NORMAL},
}


def _expand_path_pair(value: str, workspace: Path) -> tuple[Path, Path]:
    expanded = value.replace("${WORKSPACE}", str(workspace))
    candidate = Path(expanded).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate

    candidate_abs = Path(os.path.abspath(candidate))
    resolved = candidate_abs.resolve(strict=False)
    return candidate_abs, resolved


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_candidate_path(raw_path: str, workspace: Path) -> tuple[Path, Path]:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate

    candidate_abs = Path(os.path.abspath(candidate))

    try:
        resolved = candidate_abs.resolve(strict=False)
    except RuntimeError as exc:  # pragma: no cover - extremely rare cycles
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"Unable to resolve path '{raw_path}' due to symlink loop",
            {"path": raw_path},
        ) from exc

    return candidate_abs, resolved


@dataclass
class Policy:
    raw: Dict[str, Any]

    @staticmethod
    def load(path: Path) -> "Policy":
        parsed: Dict[str, Any] = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text()) or {}
            if not isinstance(loaded, dict):
                raise _invalid_argument("Policy file must parse to an object")
            parsed = _normalize_policy_legacy_aliases(loaded)
        merged = _deep_merge(DEFAULT_POLICY, parsed)
        return Policy(raw=merged)

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

    def limits(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], self.raw.get("limits", {}))

    def limit_int(self, key: str, default: int) -> int:
        val = self.limits().get(key, default)
        try:
            return int(val)
        except Exception as exc:
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

    def exec_config(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], self.raw.get("exec", {}))

    def dangerous_config(self) -> Dict[str, Any]:
        return cast(Dict[str, Any], self.raw.get("dangerous", {}))

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

    def workspace_root(self) -> Path:
        value = str(self.raw.get("workspace_root", "~/openminion_tool_runs"))
        return Path(value).expanduser().resolve(strict=False)

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
