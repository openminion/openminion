from collections.abc import Mapping
from dataclasses import dataclass

from .constants import (
    DEFAULT_GITHUB_WRITE_ALLOWED_BASE_BRANCHES,
    DEFAULT_GITHUB_WRITE_ALLOWED_BRANCH_PREFIXES,
    DEFAULT_GITHUB_WRITE_ALLOWED_PATH_PREFIXES,
    DEFAULT_GITHUB_WRITE_ALLOWED_REPOSITORIES,
)


def _coerce_str_tuple(value: object, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        token = value.strip()
        return (token,) if token else default
    if not isinstance(value, (list, tuple, set)):
        return default
    tokens = tuple(str(item).strip() for item in value if str(item).strip())
    return tokens or default


def _coerce_bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class GithubToolProfileConfig:
    """Per-profile github tool-family config (V1)."""

    token_env: str = ""
    allowed_repositories: tuple[str, ...] = DEFAULT_GITHUB_WRITE_ALLOWED_REPOSITORIES
    allowed_branch_prefixes: tuple[str, ...] = (
        DEFAULT_GITHUB_WRITE_ALLOWED_BRANCH_PREFIXES
    )
    allowed_path_prefixes: tuple[str, ...] = DEFAULT_GITHUB_WRITE_ALLOWED_PATH_PREFIXES
    allowed_base_branches: tuple[str, ...] = DEFAULT_GITHUB_WRITE_ALLOWED_BASE_BRANCHES
    allow_default_branch_writes: bool = False
    allow_force_push: bool = False
    allow_merge: bool = False
    allow_delete_branch: bool = False

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, object] | None
    ) -> "GithubToolProfileConfig":
        if not isinstance(payload, Mapping):
            return cls()
        raw = payload.get("token_env")
        token_env = str(raw).strip() if isinstance(raw, str) else ""
        return cls(
            token_env=token_env,
            allowed_repositories=_coerce_str_tuple(
                payload.get("allowed_repositories"),
                default=DEFAULT_GITHUB_WRITE_ALLOWED_REPOSITORIES,
            ),
            allowed_branch_prefixes=_coerce_str_tuple(
                payload.get("allowed_branch_prefixes"),
                default=DEFAULT_GITHUB_WRITE_ALLOWED_BRANCH_PREFIXES,
            ),
            allowed_path_prefixes=_coerce_str_tuple(
                payload.get("allowed_path_prefixes"),
                default=DEFAULT_GITHUB_WRITE_ALLOWED_PATH_PREFIXES,
            ),
            allowed_base_branches=_coerce_str_tuple(
                payload.get("allowed_base_branches"),
                default=DEFAULT_GITHUB_WRITE_ALLOWED_BASE_BRANCHES,
            ),
            allow_default_branch_writes=_coerce_bool(
                payload.get("allow_default_branch_writes"),
                default=False,
            ),
            allow_force_push=_coerce_bool(
                payload.get("allow_force_push"),
                default=False,
            ),
            allow_merge=_coerce_bool(payload.get("allow_merge"), default=False),
            allow_delete_branch=_coerce_bool(
                payload.get("allow_delete_branch"),
                default=False,
            ),
        )

    def resolved_token_env(self) -> str | None:
        """Returns the override env name, or ``None`` to fall back to
        :data:`GITHUB_TOKEN_ENV`.
        """
        return self.token_env or None


def profile_config_from_context(context: object | None) -> GithubToolProfileConfig:
    profile = getattr(context, "agent_profile", None)
    overrides = getattr(profile, "provider_config_overrides", None)
    if not isinstance(overrides, Mapping):
        return GithubToolProfileConfig()
    payload = overrides.get("github")
    if not isinstance(payload, Mapping):
        return GithubToolProfileConfig()
    return GithubToolProfileConfig.from_mapping(payload)


__all__ = ["GithubToolProfileConfig", "profile_config_from_context"]
