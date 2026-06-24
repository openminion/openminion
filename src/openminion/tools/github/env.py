from collections.abc import Mapping
from typing import Any

from openminion.base.config import EnvironmentConfig

from openminion.tools.config import (
    get_tool_env,
    get_tool_env_float,
)

from .constants import (
    DEFAULT_GITHUB_API_BASE_URL,
    DEFAULT_GITHUB_TIMEOUT_SECONDS,
    GITHUB_API_BASE_URL_ENV,
    GITHUB_TIMEOUT_SECONDS_ENV,
    GITHUB_TOKEN_ENV,
)


def get_github_token(
    *,
    token_env: str | None = None,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> str:
    """Resolve the GitHub PAT through the centralized env helper."""
    name = (token_env or "").strip() or GITHUB_TOKEN_ENV
    return get_tool_env(name, default="", context=context, env=env)


def get_github_api_base_url(
    *,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> str:
    """Resolve the GitHub API base URL (defaults to ``api.github.com``)."""
    value = get_tool_env(
        GITHUB_API_BASE_URL_ENV,
        default=DEFAULT_GITHUB_API_BASE_URL,
        context=context,
        env=env,
    )
    return value or DEFAULT_GITHUB_API_BASE_URL


def get_github_timeout_seconds(
    *,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> float:
    """Resolve the per-call timeout in seconds."""
    return get_tool_env_float(
        GITHUB_TIMEOUT_SECONDS_ENV,
        default=DEFAULT_GITHUB_TIMEOUT_SECONDS,
        context=context,
        env=env,
    )


def resolve_github_pat_through_credential_boundary(
    *,
    caller_agent_id: str,
    caller_profile_id: str,
    audit_log: "CredentialAuditLog",
    token_env: str | None = None,
    scope_kind: "CredentialScopeKind" = "tool_family",
    scope_id: str = "",
    rotation_policy: "CredentialRotationPolicy" = "reload_on_auth_failure",
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> tuple[str, "CredentialRef"]:
    """Canonical GitHub PAT resolution through the CRES boundary owner."""
    from openminion.modules.runtime.credentials import (
        resolve_credential_ref,
    )
    from openminion.tools.config import resolve_tool_credential_value

    name = (token_env or "").strip() or GITHUB_TOKEN_ENV
    effective_scope_id = scope_id.strip() or caller_profile_id.strip()
    ref = resolve_credential_ref(
        "github_pat",
        scope_kind=scope_kind,
        scope_id=effective_scope_id,
        source_kind="env",
        env_name=name,
        rotation_policy=rotation_policy,
    )
    value = resolve_tool_credential_value(
        ref,
        caller_agent_id=caller_agent_id,
        caller_profile_id=caller_profile_id,
        access_site="tools.github.env.resolve_github_pat_through_credential_boundary",
        audit_log=audit_log,
        context=context,
        env=env,
    )
    return value, ref


if False:  # pragma: no cover - typing-only import to avoid cycles
    from openminion.modules.runtime.credentials import (
        CredentialAuditLog,
        CredentialRef,
        CredentialRotationPolicy,
        CredentialScopeKind,
    )


__all__ = [
    "get_github_token",
    "get_github_api_base_url",
    "get_github_timeout_seconds",
    "resolve_github_pat_through_credential_boundary",
]
