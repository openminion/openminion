"""Sandbox action specs and policy-narrowing helpers."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

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


def _optional_cast(value: Any, cast: Callable[[Any], Any]) -> Any | None:
    return None if value is None else cast(value)


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
            address_space_bytes=_optional_cast(tc.get("address_space_bytes"), int),
            cpu_seconds=_optional_cast(tc.get("cpu_seconds"), float),
            session_mode=_optional_cast(
                tc.get("session_mode"), lambda value: str(value).strip() or None
            ),
            net_mode=tc.get("net_mode", RUNTIME_NET_MODE_DENY),
            allowed_domains=list(tc.get("allowed_domains", [])),
        )

        for name in (
            "read_allow",
            "write_allow",
            "delete_allow",
            "cmd_allowlist",
            "env_allowlist",
        ):
            if name in pc:
                narrowed = _narrow_allowed_values(getattr(spec, name), pc[name])
                setattr(spec, name, narrowed)

        for name, cast_value in (
            ("timeout_s", float),
            ("max_output_bytes", int),
            ("address_space_bytes", int),
            ("cpu_seconds", float),
        ):
            if name not in pc or pc[name] is None:
                continue
            numeric_candidate = cast_value(pc[name])
            current = getattr(spec, name)
            if current is not None:
                numeric_candidate = min(current, numeric_candidate)
            setattr(spec, name, numeric_candidate)

        if "session_mode" in pc:
            session_candidate = str(pc["session_mode"] or "").strip()
            if session_candidate and spec.session_mode != session_candidate:
                spec.session_mode = session_candidate

        if "net_mode" in pc:
            if RUNTIME_NET_MODE_DENY in (pc["net_mode"], spec.net_mode):
                spec.net_mode = RUNTIME_NET_MODE_DENY
                spec.allowed_domains = []
            else:
                spec.net_mode = pc["net_mode"]

        if "allowed_domains" in pc and spec.net_mode != RUNTIME_NET_MODE_DENY:
            spec.allowed_domains = _narrow_allowed_values(
                pc["allowed_domains"], spec.allowed_domains
            )

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
