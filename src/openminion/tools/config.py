import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable, TypeVar

from openminion.base.config.env import (
    EnvironmentConfig,
    resolve_environment_config,
    resolve_environment_config_with_explicit_env,
)
from openminion.base.config.paths import resolve_data_root, resolve_home_root
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV

_F = TypeVar("_F")
_OPENMINION_WORKSPACE_ROOT_ENV = "OPENMINION_WORKSPACE_ROOT"
_OPENMINION_WORKSPACE_ENV = "OPENMINION_WORKSPACE"
_TOOL_WORKSPACE_ROOT_ENV_KEYS: tuple[str, ...] = (
    _OPENMINION_WORKSPACE_ROOT_ENV,
    _OPENMINION_WORKSPACE_ENV,
)

ToolEnv = EnvironmentConfig

_WORKSPACE_EXTRA_KEYS: tuple[str, ...] = ("workspace_root", "workspace", "cwd")


def resolve_tool_env(
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    runtime_env: Mapping[str, object] | None = None,
    process_env: Mapping[str, object] | None = None,
) -> ToolEnv:
    if env is not None:
        return resolve_environment_config_with_explicit_env(env)
    return resolve_environment_config(
        runtime_env=runtime_env,
        process_env=process_env,
    )


def resolve_tool_context_env(
    context: Any | None = None,
    *,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> ToolEnv:
    if env is not None:
        return resolve_tool_env(env=env)
    if context is None:
        return resolve_tool_env()

    direct_env = getattr(context, "env", None)
    if isinstance(direct_env, (EnvironmentConfig, Mapping)):
        return resolve_tool_env(env=direct_env)

    for attr in ("tool_context", "runtime"):
        runtime_obj = getattr(context, attr, None)
        runtime_env = getattr(runtime_obj, "env", None)
        if isinstance(runtime_env, (EnvironmentConfig, Mapping)):
            return resolve_tool_env(env=runtime_env)

    return resolve_tool_env()


def get_tool_env(
    name: str,
    default: str = "",
    *,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> str:
    owner = (
        resolve_tool_context_env(context, env=env)
        if context is not None or env is not None
        else resolve_tool_env()
    )
    return owner.get(name, default)


def get_tool_env_float(
    name: str,
    default: float = 0.0,
    *,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = get_tool_env(name, "", context=context, env=env).strip()
    try:
        value = float(raw) if raw else float(default)
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


def get_tool_env_list(
    name: str,
    *,
    default: Sequence[str] | None = None,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    separator: str = ",",
) -> tuple[str, ...]:
    owner = (
        resolve_tool_context_env(context, env=env)
        if context is not None or env is not None
        else resolve_tool_env()
    )
    raw = owner.get(name, "").strip()
    if raw:
        values = [item.strip() for item in raw.split(separator)]
    else:
        values = [str(item).strip() for item in (default or ())]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return tuple(deduped)


def resolve_tool_workspace_root(
    *,
    workspace_root: str | Path | None = None,
    context: Any | None = None,
    extras: Mapping[str, Any] | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    fallback: str | Path | None = None,
) -> Path:
    def _candidate(raw: Any) -> str:
        return str(raw or "").strip()

    explicit = _candidate(workspace_root)
    if explicit:
        return Path(explicit).expanduser().resolve(strict=False)

    if context is not None:
        for attr in ("workspace_root", "workspace", "cwd"):
            candidate = _candidate(getattr(context, attr, ""))
            if candidate:
                return Path(candidate).expanduser().resolve(strict=False)

    merged_extras: Mapping[str, Any] = extras or {}
    if context is not None and not merged_extras:
        context_extras = getattr(context, "extras", None)
        if isinstance(context_extras, Mapping):
            merged_extras = context_extras
    for key in _WORKSPACE_EXTRA_KEYS:
        candidate = _candidate(merged_extras.get(key, ""))
        if candidate:
            return Path(candidate).expanduser().resolve(strict=False)

    if context is not None:
        for attr in ("tool_context", "runtime"):
            runtime_obj = getattr(context, attr, None)
            if runtime_obj is None:
                continue
            candidate = _candidate(getattr(runtime_obj, "workspace", ""))
            if candidate:
                return Path(candidate).expanduser().resolve(strict=False)

    env_owner = resolve_tool_context_env(context, env=env)
    for name in _TOOL_WORKSPACE_ROOT_ENV_KEYS:
        candidate = env_owner.get(name, "").strip()
        if candidate:
            return Path(candidate).expanduser().resolve(strict=False)

    fallback_path = Path(fallback).expanduser() if fallback is not None else Path.cwd()
    return fallback_path.resolve(strict=False)


def workspace_retry_path(raw_path: str) -> str:
    candidate = Path(str(raw_path or "")).expanduser()
    if (
        candidate.is_absolute()
        and len(candidate.parts) >= 3
        and candidate.parts[1] == "tmp"
    ):
        return str(Path("tmp", *candidate.parts[2:]))
    name = candidate.name or "scratch.txt"
    return str(Path("tmp") / name)


def resolve_tool_data_root(
    *,
    data_root: str | Path | None = None,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
    home_root: str | Path | None = None,
) -> Path:
    env_owner = resolve_tool_context_env(context, env=env)
    resolved_home = (
        Path(home_root).expanduser().resolve(strict=False)
        if home_root is not None
        else resolve_home_root(env=env_owner)
    )
    raw_data_root = str(
        data_root or env_owner.get(OPENMINION_DATA_ROOT_ENV, "")
    ).strip()
    return resolve_data_root(
        resolved_home,
        data_root=raw_data_root or None,
        env=env_owner,
    )


def resolve_provider_register_hook(
    target: Any,
    *,
    hook_name: str,
) -> Callable[..., None] | None:
    """Resolve a provider registration hook from a callable, module, or object.

    Returns the hook callable named ``hook_name`` if found on ``target``,
    or ``None`` if not found.
    """
    if callable(target) and getattr(target, "__name__", "") == hook_name:
        return target
    fn = (
        getattr(target, hook_name, None)
        if inspect.ismodule(target)
        else getattr(target, hook_name, None)
    )
    return fn if callable(fn) else None


def resolve_tool_credential_value(
    ref: "CredentialRef",
    *,
    caller_agent_id: str,
    caller_profile_id: str,
    access_site: str,
    audit_log: "CredentialAuditLog",
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> str:
    """Canonical credential read at the tool env-adapter seam."""
    from openminion.modules.runtime.credentials import (
        assert_credential_scope,
        record_credential_access_event,
    )

    if ref.source_kind != "env":
        raise ValueError(
            "resolve_tool_credential_value only services env-source refs; "
            f"received source_kind={ref.source_kind!r}."
        )
    assert_credential_scope(
        ref,
        caller_agent_id=caller_agent_id,
        caller_profile_id=caller_profile_id,
    )
    record_credential_access_event(
        ref,
        access_site=access_site,
        caller_agent_id=caller_agent_id,
        caller_profile_id=caller_profile_id,
        decision="allowed",
        audit_log=audit_log,
    )
    owner = (
        resolve_tool_context_env(context, env=env)
        if context is not None or env is not None
        else resolve_tool_env()
    )
    return owner.get(ref.env_name, "")


if False:  # pragma: no cover - typing-only import to avoid cycles
    from openminion.modules.runtime.credentials import (
        CredentialAuditLog,
        CredentialRef,
    )


__all__ = [
    "ToolEnv",
    "get_tool_env",
    "get_tool_env_float",
    "get_tool_env_list",
    "resolve_provider_register_hook",
    "resolve_tool_context_env",
    "resolve_tool_credential_value",
    "resolve_tool_data_root",
    "resolve_tool_env",
    "resolve_tool_workspace_root",
]
