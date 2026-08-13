from collections.abc import Mapping
from pathlib import Path
from typing import Any

from openminion.base.config.env import EnvironmentConfig
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV
from openminion.tools.config import (
    resolve_tool_context_env,
    resolve_tool_data_root,
    resolve_tool_env,
)

from ..constants import DEFAULT_IDENTITY_DB_SUBPATH


def _runtime_env_payload_from_context(ctx: Any) -> Mapping[str, object] | None:
    metadata = getattr(ctx, "metadata", None)
    if isinstance(metadata, Mapping):
        raw_runtime_env = metadata.get("runtime_env")
        if isinstance(raw_runtime_env, Mapping):
            return raw_runtime_env

    raw = getattr(getattr(ctx, "policy", None), "raw", {}) or {}
    if isinstance(raw, Mapping):
        context_meta = raw.get("context_metadata")
        if isinstance(context_meta, Mapping):
            raw_runtime_env = context_meta.get("runtime_env")
            if isinstance(raw_runtime_env, Mapping):
                return raw_runtime_env
    return None


def resolve_env_from_context(ctx: Any | None = None) -> EnvironmentConfig:
    if ctx is not None:
        env = getattr(ctx, "env", None)
        if isinstance(env, EnvironmentConfig):
            return resolve_tool_context_env(ctx)
        runtime_env = _runtime_env_payload_from_context(ctx)
        if isinstance(runtime_env, Mapping):
            return resolve_tool_env(env=runtime_env)
        return resolve_tool_context_env(ctx)
    return resolve_tool_env()


def _policy_value_from_context(ctx: Any, key: str) -> str:
    raw = getattr(getattr(ctx, "policy", None), "raw", {}) or {}
    if not isinstance(raw, Mapping):
        return ""
    context_meta = raw.get("context_metadata")
    if isinstance(context_meta, Mapping):
        token = str(context_meta.get(key, "")).strip()
        if token:
            return token
    return str(raw.get(key, "")).strip()


def agent_id_from_context(ctx: Any) -> str:
    token = _policy_value_from_context(ctx, "agent_id")
    if token:
        return token
    env_token = str(
        resolve_env_from_context(ctx).get("OPENMINION_AGENT_ID", "")
    ).strip()
    if env_token:
        return env_token
    return "openminion"


def identity_db_candidates(*, env: EnvironmentConfig | None = None) -> tuple[Path, ...]:
    candidates: list[Path] = []
    resolved_env = env or resolve_tool_env()
    env_identity_db = resolved_env.get("OPENMINION_IDENTITY_DB", "").strip()
    if env_identity_db:
        candidates.append(Path(env_identity_db).expanduser())
    try:
        data_root = resolve_tool_data_root(
            data_root=resolved_env.get(OPENMINION_DATA_ROOT_ENV, ""),
            env=resolved_env,
        )
        candidates.append(data_root / DEFAULT_IDENTITY_DB_SUBPATH)
    except Exception:
        pass

    return tuple(
        dict.fromkeys(candidate.resolve(strict=False) for candidate in candidates)
    )


def storage_path_from_context(ctx: Any) -> str | None:
    token = _policy_value_from_context(ctx, "storage_path")
    if token:
        return token
    env_storage_path = (
        resolve_env_from_context(ctx).get("OPENMINION_STORAGE_PATH", "").strip()
    )
    return env_storage_path or None
