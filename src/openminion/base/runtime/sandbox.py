"""Sandbox action specs and policy-narrowing helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .constants import RUNTIME_NET_MODE_DENY


@dataclass
class ExecSpec:
    cmd: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    stdin: str | None = None


@dataclass
class FsWriteSpec:
    path: str
    content: str | bytes = ""


@dataclass
class FsDeleteSpec:
    path: str


@dataclass
class NetFetchSpec:
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass
class FsResult:
    success: bool
    path: str
    error: str | None = None


@dataclass
class NetResult:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def _narrow_allowed_values(
    values: list[str], allowed_values: Iterable[str]
) -> list[str]:
    allowed = set(allowed_values)
    return [value for value in values if value in allowed]


@dataclass
class ExecutionSandboxSpec:
    workspace_root: str

    read_allow: list[str] = field(default_factory=list)
    write_allow: list[str] = field(default_factory=list)
    delete_allow: list[str] = field(default_factory=list)
    ro_mounts: list[str] = field(default_factory=list)

    cmd_allowlist: list[str] = field(default_factory=list)
    env_allowlist: list[str] = field(default_factory=list)
    timeout_s: float = 30.0
    max_output_bytes: int = 1_048_576  # 1 MiB
    address_space_bytes: int | None = None
    cpu_seconds: float | None = None
    session_mode: str | None = None

    net_mode: str = RUNTIME_NET_MODE_DENY
    allowed_domains: list[str] = field(default_factory=list)

    idempotency_key: str | None = None

    @classmethod
    def build(
        cls,
        *,
        workspace_root: str,
        tool_caps: dict[str, Any] | None = None,
        policy_constraints: dict[str, Any] | None = None,
    ) -> "ExecutionSandboxSpec":
        tc = tool_caps or {}
        pc = policy_constraints or {}

        spec = cls(
            workspace_root=workspace_root,
            read_allow=list(tc.get("read_allow", [workspace_root])),
            write_allow=list(tc.get("write_allow", [workspace_root])),
            delete_allow=list(tc.get("delete_allow", [])),
            ro_mounts=list(tc.get("ro_mounts", [])),
            cmd_allowlist=list(tc.get("cmd_allowlist", [])),
            env_allowlist=list(tc.get("env_allowlist", [])),
            timeout_s=float(tc.get("timeout_s", 30.0)),
            max_output_bytes=int(tc.get("max_output_bytes", 1_048_576)),
            address_space_bytes=(
                None
                if tc.get("address_space_bytes") is None
                else int(tc["address_space_bytes"])
            ),
            cpu_seconds=(
                None if tc.get("cpu_seconds") is None else float(tc["cpu_seconds"])
            ),
            session_mode=(
                None
                if tc.get("session_mode") is None
                else str(tc["session_mode"]).strip() or None
            ),
            net_mode=tc.get("net_mode", RUNTIME_NET_MODE_DENY),
            allowed_domains=list(tc.get("allowed_domains", [])),
        )

        if "read_allow" in pc:
            spec.read_allow = _narrow_allowed_values(spec.read_allow, pc["read_allow"])

        if "write_allow" in pc:
            spec.write_allow = _narrow_allowed_values(
                spec.write_allow, pc["write_allow"]
            )

        if "delete_allow" in pc:
            spec.delete_allow = _narrow_allowed_values(
                spec.delete_allow, pc["delete_allow"]
            )

        if "cmd_allowlist" in pc:
            spec.cmd_allowlist = _narrow_allowed_values(
                spec.cmd_allowlist, pc["cmd_allowlist"]
            )

        if "env_allowlist" in pc:
            spec.env_allowlist = _narrow_allowed_values(
                spec.env_allowlist, pc["env_allowlist"]
            )

        if "timeout_s" in pc:
            spec.timeout_s = min(spec.timeout_s, float(pc["timeout_s"]))

        if "max_output_bytes" in pc:
            spec.max_output_bytes = int(
                min(spec.max_output_bytes, int(pc["max_output_bytes"]))
            )

        if "address_space_bytes" in pc:
            candidate = pc["address_space_bytes"]
            if candidate is not None:
                narrowed = int(candidate)
                if spec.address_space_bytes is None:
                    spec.address_space_bytes = narrowed
                else:
                    spec.address_space_bytes = min(spec.address_space_bytes, narrowed)

        if "cpu_seconds" in pc:
            candidate = pc["cpu_seconds"]
            if candidate is not None:
                cpu_narrowed = float(candidate)
                if spec.cpu_seconds is None:
                    spec.cpu_seconds = cpu_narrowed
                else:
                    spec.cpu_seconds = min(spec.cpu_seconds, cpu_narrowed)

        if "session_mode" in pc:
            candidate = str(pc["session_mode"] or "").strip()
            if candidate and str(spec.session_mode or "").strip() != candidate:
                spec.session_mode = candidate

        if "net_mode" in pc:
            if (
                pc["net_mode"] == RUNTIME_NET_MODE_DENY
                or spec.net_mode == RUNTIME_NET_MODE_DENY
            ):
                spec.net_mode = RUNTIME_NET_MODE_DENY
                spec.allowed_domains = []
            else:
                spec.net_mode = pc["net_mode"]

        if "allowed_domains" in pc and spec.net_mode != RUNTIME_NET_MODE_DENY:
            tool_domains = set(spec.allowed_domains)
            spec.allowed_domains = [
                d for d in pc["allowed_domains"] if d in tool_domains
            ]

        return spec


@runtime_checkable
class SandboxRunner(Protocol):
    name: str

    def run_exec(self, spec: ExecSpec, sandbox: ExecutionSandboxSpec) -> ExecResult: ...

    def fs_write(
        self, spec: FsWriteSpec, sandbox: ExecutionSandboxSpec
    ) -> FsResult: ...

    def fs_delete(
        self, spec: FsDeleteSpec, sandbox: ExecutionSandboxSpec
    ) -> FsResult: ...

    def net_fetch(
        self, spec: NetFetchSpec, sandbox: ExecutionSandboxSpec
    ) -> NetResult: ...
