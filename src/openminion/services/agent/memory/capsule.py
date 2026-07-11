from pathlib import Path
from typing import Any

from openminion.base.config import OpenMinionConfig
from openminion.services.config import normalize_memory_capsule_strategy
from openminion.services.constants import (
    MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN,
    MEMORY_CAPSULE_STRATEGY_FROZEN_SESSION,
    MEMORY_CAPSULE_STRATEGY_OFF,
    MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE,
)
from openminion.services.bootstrap.paths import SERVICES_MEMORY_SUBDIR

MEMORY_POLICY_SNAPSHOT_VERSION = "memory_policy_snapshot.v1"
MEMORY_ENVELOPE_VERSION = "memory_envelope.v1"
MEMORY_META_VERSION = "memory_meta.v1"
MEMORY_PATCH_VERSION = "memory_patch.v1"


def normalize_memory_provider(raw: Any) -> str:
    normalized = str(raw or "").strip().lower()
    if normalized in {"", "memory_v2"}:
        return "memory_v2"
    if normalized in {"memory_v2_smoke", "memory_v2_hello_world"}:
        return "memory_v2_smoke"
    raise ValueError(
        f"Unsupported memory_provider={raw!r}. "
        "Supported values: memory_v2, memory_v2_smoke (memory_v2_hello_world is a legacy alias)."
    )


def build_memory_policy_snapshot(*, config: OpenMinionConfig) -> dict[str, Any]:
    runtime = getattr(config, "runtime", None)
    if runtime is None:
        raise ValueError("runtime config is unavailable")

    memory_enabled = bool(getattr(runtime, "memory_enabled", True))
    capsule_strategy = normalize_memory_capsule_strategy(
        getattr(
            runtime, "memory_capsule_strategy", MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN
        )
    )
    dynamic_retrieval_enabled = bool(
        getattr(runtime, "memory_dynamic_retrieval_enabled", True)
    )
    memory_provider = normalize_memory_provider(
        getattr(runtime, "memory_provider", "memory_v2")
    )
    retention_days = max(1, int(getattr(runtime, "memory_log_retention_days", 30)))

    refresh_policy = {
        MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN: "refresh_each_turn",
        MEMORY_CAPSULE_STRATEGY_FROZEN_SESSION: "refresh_once_per_session",
        MEMORY_CAPSULE_STRATEGY_REFRESH_ON_WRITE: "refresh_on_memory_change",
        MEMORY_CAPSULE_STRATEGY_OFF: "disabled",
    }.get(capsule_strategy, "refresh_each_turn")

    session_vs_cross_session = (
        "session_plus_cross_session" if memory_enabled else "session_only"
    )

    return {
        "policy_version": MEMORY_POLICY_SNAPSHOT_VERSION,
        "policy_source": "runtime.config",
        "memory_enabled": memory_enabled,
        "capsule_strategy": capsule_strategy,
        "refresh_policy": refresh_policy,
        "dynamic_retrieval_enabled": dynamic_retrieval_enabled,
        "memory_provider": memory_provider,
        "retention_days": retention_days,
        "session_vs_cross_session": session_vs_cross_session,
        "cross_session_memory_enabled": memory_enabled,
    }


def resolve_memory_root(
    *,
    config: OpenMinionConfig,
    config_path: Path,
    storage_path: Path,
    data_root: Path | None = None,
) -> Path:
    configured = str(getattr(config.runtime, "memory_root_path", "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    if data_root is not None:
        return (data_root / SERVICES_MEMORY_SUBDIR).resolve(strict=False)
    if config_path:
        return (config_path.parent / SERVICES_MEMORY_SUBDIR).resolve(strict=False)
    return (storage_path.parent / SERVICES_MEMORY_SUBDIR).resolve(strict=False)


__all__ = [
    "MEMORY_POLICY_SNAPSHOT_VERSION",
    "MEMORY_ENVELOPE_VERSION",
    "MEMORY_META_VERSION",
    "MEMORY_PATCH_VERSION",
    "build_memory_policy_snapshot",
    "normalize_memory_capsule_strategy",
    "normalize_memory_provider",
    "resolve_memory_root",
]
