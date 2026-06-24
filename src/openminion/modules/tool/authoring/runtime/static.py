"""Static inspection for authored tools."""

import ast
from dataclasses import dataclass
import re
import sys
from typing import Iterable


READ_SAFE = "READ_SAFE"
WRITE_SAFE = "WRITE_SAFE"
POWER_USER = "POWER_USER"

_RISK_ORDER = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}

_SECRET_TOKEN_RE = re.compile(
    r"(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{10,}|xoxb-[A-Za-z0-9-]{10,})"
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9_ ]+PRIVATE KEY-----")
_NETWORK_IMPORT_ROOTS = {"urllib", "httpx", "requests"}
_NATIVE_IMPORT_ROOTS = {"ctypes", "cffi"}
_STD_LIB_MODULES = set(getattr(sys, "stdlib_module_names", set()))


@dataclass(frozen=True)
class StaticInspectFinding:
    code: str
    severity: str
    message: str
    location: str


def _attr_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _bool_keyword(node: ast.Call, *, name: str) -> bool:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return bool(keyword.value.value)
    return False


def _call_name(node: ast.Call) -> tuple[str, ...]:
    if isinstance(node.func, ast.Name):
        return (node.func.id,)
    return _attr_chain(node.func)


def _literal_strings(tree: ast.AST) -> Iterable[ast.Constant]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node


def _make_finding(
    code: str,
    severity: str,
    message: str,
    lineno: int | None,
) -> StaticInspectFinding:
    return StaticInspectFinding(
        code=code,
        severity=severity,
        message=message,
        location=f"line {lineno or 0}",
    )


def rollup_risk_level(findings: Iterable[StaticInspectFinding]) -> str:
    level = "low"
    for finding in findings:
        severity = str(finding.severity or "low").strip().lower() or "low"
        if _RISK_ORDER.get(severity, 0) > _RISK_ORDER[level]:
            level = severity
    return level


def inspect_source(
    source_code: str,
    *,
    target_scope_tier: str = POWER_USER,
    allowed_deps: set[str] | None = None,
) -> tuple[str, list[StaticInspectFinding]]:
    tree = ast.parse(str(source_code or ""))
    findings: list[StaticInspectFinding] = []
    tier = str(target_scope_tier or POWER_USER).strip().upper() or POWER_USER
    allowed = {
        str(item).strip() for item in (allowed_deps or set()) if str(item).strip()
    }

    def add(code: str, severity: str, message: str, node: ast.AST) -> None:
        findings.append(
            _make_finding(code, severity, message, getattr(node, "lineno", None))
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name_chain = _call_name(node)
            if name_chain in {("exec",), ("eval",), ("compile",), ("__import__",)}:
                add("STATIC-DYN-001", "critical", "dynamic execution call", node)
            if name_chain == ("getattr",):
                if (
                    len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == "builtins"
                ):
                    add(
                        "STATIC-DYN-002",
                        "critical",
                        "builtins getattr pattern",
                        node,
                    )
            if name_chain == ("os", "system") or (
                len(name_chain) >= 2
                and name_chain[0] == "subprocess"
                and _bool_keyword(node, name="shell")
            ):
                add("STATIC-SHELL-001", "critical", "shell execution pattern", node)
            if tier == READ_SAFE:
                if name_chain == ("open",) and len(node.args) >= 2:
                    mode_arg = node.args[1]
                    if isinstance(mode_arg, ast.Constant) and isinstance(
                        mode_arg.value, str
                    ):
                        if any(ch in mode_arg.value for ch in ("w", "a", "x", "+")):
                            add(
                                "STATIC-FS-WRITE",
                                "high",
                                "filesystem write pattern",
                                node,
                            )
                if name_chain[:2] == ("pathlib", "Path"):
                    continue
                if name_chain[-1:] and name_chain[-1] in {"write_text", "write_bytes"}:
                    add("STATIC-FS-WRITE", "high", "filesystem write pattern", node)
                if name_chain in {("os", "remove"), ("os", "unlink")} or (
                    len(name_chain) >= 1 and name_chain[0] == "shutil"
                ):
                    add("STATIC-FS-WRITE", "high", "filesystem write pattern", node)
                if len(name_chain) >= 1 and name_chain[0] in {"subprocess"}:
                    add("STATIC-SUBPROC", "high", "subprocess invocation", node)
                if (
                    len(name_chain) >= 2
                    and name_chain[0] == "os"
                    and name_chain[1].startswith("exec")
                ):
                    add("STATIC-SUBPROC", "high", "os.exec* invocation", node)
            if tier == WRITE_SAFE:
                if len(name_chain) >= 1 and name_chain[0] == "subprocess":
                    add("STATIC-SUBPROC-WS", "high", "subprocess invocation", node)
                if (
                    len(name_chain) >= 2
                    and name_chain[0] == "os"
                    and name_chain[1].startswith("exec")
                ):
                    add("STATIC-SUBPROC-WS", "high", "os.exec* invocation", node)

        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif node.module:
                names = [node.module.split(".", 1)[0]]

            for root_name in names:
                if root_name in _NATIVE_IMPORT_ROOTS:
                    add(
                        "STATIC-NATIVE-001", "critical", "native extension import", node
                    )
                if root_name == "socket":
                    add("STATIC-NATIVE-002", "critical", "raw socket import", node)
                if tier == READ_SAFE and root_name in _NETWORK_IMPORT_ROOTS:
                    add("STATIC-NET-EGRESS", "high", "network egress import", node)
                if tier == WRITE_SAFE and root_name in (
                    _NETWORK_IMPORT_ROOTS | {"socket"}
                ):
                    add("STATIC-NET-RAW", "high", "raw network import", node)
                if tier == POWER_USER:
                    if (
                        root_name
                        and root_name not in _STD_LIB_MODULES
                        and root_name not in allowed
                    ):
                        add(
                            "STATIC-DEP-DENY",
                            "high",
                            f"dependency '{root_name}' not allowed",
                            node,
                        )

    for node in _literal_strings(tree):
        value = str(node.value)
        if _SECRET_TOKEN_RE.search(value):
            add("STATIC-SECRET-001", "critical", "hardcoded token literal", node)
        if _PRIVATE_KEY_RE.search(value):
            add("STATIC-SECRET-002", "critical", "private key literal", node)
        if tier == POWER_USER and (
            "api.anthropic.com" in value or "claude.ai" in value
        ):
            add(
                "STATIC-NET-ANTHROPIC",
                "medium",
                "anthropic endpoint reference",
                node,
            )
        if tier == WRITE_SAFE and (
            value.startswith("/") or ".." in value.replace("\\", "/")
        ):
            add(
                "STATIC-FS-ESCAPE",
                "high",
                "known-bad literal path pattern",
                node,
            )

    return rollup_risk_level(findings), findings
