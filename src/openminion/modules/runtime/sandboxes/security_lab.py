"""Contracts and strict config for the opt-in local security lab."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config.base import ConfigError
from openminion.base.runtime.sandbox import ExecResult, ExecutionSandboxSpec

_DIGEST_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
_FIELDS = {
    "daemon_socket",
    "target_container",
    "worker_image",
    "executable_allowlist",
    "worker_uid",
    "worker_gid",
    "agent_identity_id",
    "command_timeout_seconds",
    "max_output_bytes",
    "cpu_limit",
    "memory_bytes",
    "pids_limit",
    "label",
}


@dataclass(frozen=True)
class SecurityLabConfig:
    daemon_socket: str
    target_container: str
    worker_image: str
    executable_allowlist: tuple[str, ...]
    worker_uid: int
    worker_gid: int
    agent_identity_id: str
    command_timeout_seconds: int
    max_output_bytes: int
    cpu_limit: float
    memory_bytes: int
    pids_limit: int
    label: str


@dataclass(frozen=True)
class SecurityLabExecutionScope:
    session_id: str
    activation_id: str
    config_fingerprint: str
    resolved_scope_fingerprint: str
    daemon_id: str
    target_container_id: str
    target_image_id: str
    worker_image_digest: str


@dataclass
class SecurityLabExecutionSpec(ExecutionSandboxSpec):  # type: ignore[misc]
    security_lab: SecurityLabExecutionScope | None = None


@dataclass
class SecurityLabExecResult(ExecResult):  # type: ignore[misc]
    stdout_bytes: bytes | None = None
    stderr_bytes: bytes | None = None
    stdout_observed_bytes: int | None = None
    stderr_observed_bytes: int | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    output_limited: bool = False
    terminated: bool = False
    execution_facts: SecurityLabExecutionFacts | None = None


@dataclass(frozen=True)
class SecurityLabExecutionFacts:
    runner: str
    worker_container_id: str
    worker_image_digest: str
    daemon_id: str
    target_container_id: str
    target_image_id: str
    isolation_mode: str
    started_at: str
    duration_ms: int
    executable_basename: str
    argument_count: int
    canonical_argv_sha256: str
    timeout_seconds: float
    max_output_bytes: int
    cpu_limit: float
    memory_bytes: int
    pids_limit: int


def _required_text(value: object, field_path: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ConfigError(f"{field_path} is required")
    return token


def _positive_int(value: object, field_path: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_path} must be a positive integer") from exc
    if parsed <= 0:
        raise ConfigError(f"{field_path} must be a positive integer")
    return parsed


def _positive_float(value: object, field_path: str) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field_path} must be greater than zero") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ConfigError(f"{field_path} must be greater than zero")
    return parsed


def coerce_security_lab_config(
    value: object,
    *,
    field_path: str = "runtime.security_lab",
) -> SecurityLabConfig | None:
    if value is None:
        return None
    if isinstance(value, SecurityLabConfig):
        return value
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_path} must be an object")
    unknown = sorted(set(value) - _FIELDS)
    if unknown:
        raise ConfigError(f"{field_path} has unsupported fields: {', '.join(unknown)}")

    daemon_socket = _required_text(
        value.get("daemon_socket"), f"{field_path}.daemon_socket"
    )
    if not daemon_socket.startswith("unix://"):
        raise ConfigError(f"{field_path}.daemon_socket must use unix://")
    if not Path(daemon_socket.removeprefix("unix://")).is_absolute():
        raise ConfigError(f"{field_path}.daemon_socket must be an absolute path")

    worker_image = _required_text(
        value.get("worker_image"), f"{field_path}.worker_image"
    )
    if not _DIGEST_IMAGE.fullmatch(worker_image):
        raise ConfigError(f"{field_path}.worker_image must include a sha256 digest")

    raw_allowlist = value.get("executable_allowlist")
    if not isinstance(raw_allowlist, (list, tuple)) or not raw_allowlist:
        raise ConfigError(
            f"{field_path}.executable_allowlist must be a non-empty array"
        )
    allowlist = tuple(str(item or "").strip() for item in raw_allowlist)
    if any(not item for item in allowlist) or len(set(allowlist)) != len(allowlist):
        raise ConfigError(
            f"{field_path}.executable_allowlist must contain unique non-empty values"
        )

    return SecurityLabConfig(
        daemon_socket=daemon_socket,
        target_container=_required_text(
            value.get("target_container"), f"{field_path}.target_container"
        ),
        worker_image=worker_image,
        executable_allowlist=allowlist,
        worker_uid=_positive_int(value.get("worker_uid"), f"{field_path}.worker_uid"),
        worker_gid=_positive_int(value.get("worker_gid"), f"{field_path}.worker_gid"),
        agent_identity_id=_required_text(
            value.get("agent_identity_id"), f"{field_path}.agent_identity_id"
        ),
        command_timeout_seconds=_positive_int(
            value.get("command_timeout_seconds"),
            f"{field_path}.command_timeout_seconds",
        ),
        max_output_bytes=_positive_int(
            value.get("max_output_bytes"), f"{field_path}.max_output_bytes"
        ),
        cpu_limit=_positive_float(value.get("cpu_limit"), f"{field_path}.cpu_limit"),
        memory_bytes=_positive_int(
            value.get("memory_bytes"), f"{field_path}.memory_bytes"
        ),
        pids_limit=_positive_int(value.get("pids_limit"), f"{field_path}.pids_limit"),
        label=_required_text(value.get("label"), f"{field_path}.label"),
    )


def security_lab_config_to_dict(config: SecurityLabConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["executable_allowlist"] = list(config.executable_allowlist)
    return payload


__all__ = [
    "SecurityLabConfig",
    "SecurityLabExecutionFacts",
    "SecurityLabExecutionScope",
    "SecurityLabExecutionSpec",
    "SecurityLabExecResult",
    "coerce_security_lab_config",
    "security_lab_config_to_dict",
]
