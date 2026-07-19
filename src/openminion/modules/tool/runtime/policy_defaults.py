from __future__ import annotations

from typing import Any, Dict

from ..constants import (
    TOOL_AUDIT_WRITE_MODE_DUAL,
    TOOL_DANGEROUS_MODE_PROMPT,
    TOOL_EXEC_ASK_ON_MISS,
    TOOL_EXEC_SECURITY_ALLOWLIST,
    TOOL_EXEC_SECURITY_DENY,
    TOOL_REDACTION_MODE_NORMAL,
)
from .command_patterns import COMMAND_ALLOW_PATTERNS, DISCOVERY_KNOWN_TOOLS


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


__all__ = ["DEFAULT_POLICY"]
