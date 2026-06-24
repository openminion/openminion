from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.base.config import OpenMinionConfig, resolve_data_root
from openminion.base.config.env import resolve_environment_config
from openminion.base.config.paths import resolve_config_storage_path
from openminion.modules.config import resolve_module_home_root
from .constants import (
    A2A_POLICY_ACTION_ALLOW,
    DEFAULT_ARTIFACTS_DIRNAME,
    DEFAULT_AUDIT_DIRNAME,
    DEFAULT_INTEGRATED_ROOT_SUBPATH,
    DEFAULT_STATE_FILENAME,
    DEFAULT_STANDALONE_ARTIFACT_SUBPATH,
    DEFAULT_STANDALONE_AUDIT_SUBPATH,
    DEFAULT_STANDALONE_STATE_SUBPATH,
)

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


@dataclass
class StateConfig:
    backend: str = "sqlite"
    path: str = "~/.a2actl/state.db"


@dataclass
class AuditConfig:
    backend: str = "sqlite_rotated"
    root: str = "~/.a2actl/audit"
    retention_days: int = 14


@dataclass
class StorageConfig:
    state: StateConfig = field(default_factory=StateConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)


@dataclass
class ArtifactConfig:
    root: str = "~/.a2actl/artifacts"
    max_inline_bytes: int = 16_384


@dataclass
class RecoveryConfig:
    stale_heartbeat_sec: int = 300


@dataclass
class PolicyConfig:
    default_action: str = A2A_POLICY_ACTION_ALLOW
    rules: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TransportConfig:
    mode: str = "inproc"


@dataclass
class RuntimeConfig:
    version: int = 1
    transport: TransportConfig = field(default_factory=TransportConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    artifacts: ArtifactConfig = field(default_factory=ArtifactConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> RuntimeConfig:
    del base_config
    default_state_path, default_audit_root, default_artifact_root = _default_paths(
        home_root, data_root
    )
    return _default_runtime_config(
        default_state_path,
        default_audit_root,
        default_artifact_root,
    )


def load_config(path: str | Path | dict[str, Any] | RuntimeConfig) -> RuntimeConfig:
    if isinstance(path, RuntimeConfig):
        return path

    env = resolve_environment_config()
    home_root = resolve_module_home_root(None, env)
    data_root = (
        resolve_data_root(home_root, data_root=env.openminion_data_root or None)
        if home_root is not None
        else None
    )
    default_state_path, default_audit_root, default_artifact_root = _default_paths(
        home_root, data_root
    )

    if isinstance(path, dict):
        raw = dict(path)
    else:
        cfg_path = Path(path).expanduser().resolve(strict=False)
        if not cfg_path.exists():
            return _default_runtime_config(
                default_state_path,
                default_audit_root,
                default_artifact_root,
            )
        text = cfg_path.read_text(encoding="utf-8")
        if cfg_path.suffix.lower() in {".json"}:
            parsed = json.loads(text or "{}")
        else:
            if yaml is None:
                raise RuntimeError("PyYAML is required for YAML config files")
            parsed = yaml.safe_load(text) or {}
        if not isinstance(parsed, dict):
            raise ValueError("a2actl config must be an object")
        raw = parsed

    transport = _obj(raw.get("transport"), {})
    storage = _obj(raw.get("storage"), {})
    state = _obj(storage.get("state"), {})
    audit = _obj(storage.get("audit"), {})
    artifacts = _obj(raw.get("artifacts"), {})
    recovery = _obj(raw.get("recovery"), {})
    policy = _obj(raw.get("policy"), {})

    state_path = resolve_config_storage_path(
        str(state.get("path", default_state_path)),
        data_root=data_root,
        label="a2a_state_path",
    )
    audit_root = resolve_config_storage_path(
        str(audit.get("root", default_audit_root)),
        data_root=data_root,
        label="a2a_audit_root",
    )
    artifact_root = resolve_config_storage_path(
        str(artifacts.get("root", default_artifact_root)),
        data_root=data_root,
        label="a2a_artifact_root",
    )

    return RuntimeConfig(
        version=int(raw.get("version", 1)),
        transport=TransportConfig(mode=str(transport.get("mode", "inproc"))),
        storage=StorageConfig(
            state=StateConfig(
                backend=str(state.get("backend", "sqlite")),
                path=state_path,
            ),
            audit=AuditConfig(
                backend=str(audit.get("backend", "sqlite_rotated")),
                root=audit_root,
                retention_days=int(audit.get("retention_days", 14)),
            ),
        ),
        artifacts=ArtifactConfig(
            root=artifact_root,
            max_inline_bytes=int(artifacts.get("max_inline_bytes", 16_384)),
        ),
        recovery=RecoveryConfig(
            stale_heartbeat_sec=int(recovery.get("stale_heartbeat_sec", 300)),
        ),
        policy=PolicyConfig(
            default_action=str(policy.get("default_action", A2A_POLICY_ACTION_ALLOW)),
            rules=list(policy.get("rules", []))
            if isinstance(policy.get("rules"), list)
            else [],
        ),
    )


def _default_runtime_config(
    default_state_path: str,
    default_audit_root: str,
    default_artifact_root: str,
) -> RuntimeConfig:
    return RuntimeConfig(
        storage=StorageConfig(
            state=StateConfig(path=default_state_path),
            audit=AuditConfig(root=default_audit_root),
        ),
        artifacts=ArtifactConfig(root=default_artifact_root),
    )


def _obj(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance(value, dict) else dict(default)


def _default_paths(
    home_root: Path | None, data_root: Path | None
) -> tuple[str, str, str]:
    if home_root is None or data_root is None:
        return (
            str(DEFAULT_STANDALONE_STATE_SUBPATH),
            str(DEFAULT_STANDALONE_AUDIT_SUBPATH),
            str(DEFAULT_STANDALONE_ARTIFACT_SUBPATH),
        )
    base = (data_root / DEFAULT_INTEGRATED_ROOT_SUBPATH).resolve(strict=False)
    return (
        str((base / DEFAULT_STATE_FILENAME).resolve(strict=False)),
        str((base / DEFAULT_AUDIT_DIRNAME).resolve(strict=False)),
        str((base / DEFAULT_ARTIFACTS_DIRNAME).resolve(strict=False)),
    )
