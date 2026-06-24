from collections.abc import Mapping
from typing import Any

from openminion.base.config import EnvironmentConfig

from openminion.modules.tool.errors import ToolRuntimeError

from .config import GithubToolProfileConfig
from .env import get_github_token


def require_github_pat(
    *,
    profile: GithubToolProfileConfig | Mapping[str, object] | None = None,
    token_env: str | None = None,
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> str:
    """Resolve the GitHub PAT or raise a deterministic ``AUTH_REQUIRED``."""
    resolved_env = (token_env or "").strip()
    if not resolved_env and profile is not None:
        if isinstance(profile, GithubToolProfileConfig):
            resolved_env = profile.token_env
        elif isinstance(profile, Mapping):
            cfg = GithubToolProfileConfig.from_mapping(profile)
            resolved_env = cfg.token_env

    pat = get_github_token(token_env=resolved_env or None, context=context, env=env)
    if not pat:
        raise ToolRuntimeError(
            "AUTH_REQUIRED",
            "GitHub PAT is not configured.",
            {
                "reason_code": "github_pat_missing",
                "env_name": (resolved_env or "GITHUB_TOKEN"),
            },
        )
    return pat


def auth_invalid_error(
    *,
    status_code: int | None,
    body_excerpt: str = "",
) -> ToolRuntimeError:
    """Construct the canonical ``AUTH_INVALID`` error for 401/403 responses."""
    return ToolRuntimeError(
        "AUTH_INVALID",
        "GitHub PAT was rejected by the API.",
        {
            "reason_code": "github_pat_invalid",
            "status_code": status_code,
            "body_excerpt": body_excerpt[:200],
        },
    )


def reload_github_pat_after_auth_invalid(
    ref: "CredentialRef",
    *,
    audit_log: "CredentialAuditLog",
    context: Any | None = None,
    env: EnvironmentConfig | Mapping[str, object] | None = None,
) -> tuple[str, "CredentialRotationEvent"]:
    """Owned reload path for the typed ``AUTH_INVALID`` response."""
    from openminion.modules.runtime.credentials import (
        reload_credential_after_auth_failure,
    )

    event = reload_credential_after_auth_failure(ref, audit_log=audit_log)
    value = get_github_token(token_env=ref.env_name, context=context, env=env)
    return value, event


if False:  # pragma: no cover - typing-only import to avoid cycles
    from openminion.modules.runtime.credentials import (
        CredentialAuditLog,
        CredentialRef,
        CredentialRotationEvent,
    )


__all__ = [
    "auth_invalid_error",
    "reload_github_pat_after_auth_invalid",
    "require_github_pat",
]
