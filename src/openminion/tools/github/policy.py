from collections.abc import Iterable
from typing import Any

from openminion.modules.tool.errors import ToolRuntimeError

from .config import GithubToolProfileConfig, profile_config_from_context
from .constants import (
    GITHUB_POLICY_DENIED_BASE_BRANCH,
    GITHUB_POLICY_DENIED_BRANCH_PREFIX,
    GITHUB_POLICY_DENIED_DEFAULT_BRANCH,
    GITHUB_POLICY_DENIED_DELETE,
    GITHUB_POLICY_DENIED_FORCE_PUSH,
    GITHUB_POLICY_DENIED_MERGE,
    GITHUB_POLICY_DENIED_PATH_PREFIX,
    GITHUB_POLICY_DENIED_PR_HEAD,
    GITHUB_POLICY_DENIED_REPO,
)


def github_write_policy_from_context(ctx: Any | None) -> GithubToolProfileConfig:
    return profile_config_from_context(ctx)


def _deny(reason_code: str, message: str, **details: object) -> None:
    raise ToolRuntimeError(
        "POLICY_DENIED",
        message,
        {"reason_code": reason_code, **details},
    )


def ensure_repository_allowed(
    *,
    owner: str,
    repo: str,
    config: GithubToolProfileConfig,
) -> None:
    token = f"{owner}/{repo}"
    if token in config.allowed_repositories:
        return
    _deny(
        GITHUB_POLICY_DENIED_REPO,
        f"GitHub write actions are not allowed for {token!r}.",
        repository=token,
        allowed_repositories=list(config.allowed_repositories),
    )


def ensure_branch_allowed(
    *,
    branch: str,
    base_branch: str,
    config: GithubToolProfileConfig,
) -> None:
    if not config.allow_default_branch_writes and branch == base_branch:
        _deny(
            GITHUB_POLICY_DENIED_DEFAULT_BRANCH,
            "Direct writes to the base/default branch are not allowed in L3.",
            branch=branch,
            base_branch=base_branch,
        )
    if any(branch.startswith(prefix) for prefix in config.allowed_branch_prefixes):
        return
    _deny(
        GITHUB_POLICY_DENIED_BRANCH_PREFIX,
        "GitHub write branch is outside the allowed smoke prefixes.",
        branch=branch,
        allowed_branch_prefixes=list(config.allowed_branch_prefixes),
    )


def ensure_base_branch_allowed(
    *,
    base_branch: str,
    config: GithubToolProfileConfig,
) -> None:
    """Per RWPRS §5.2: ``github.open_pr`` base branches are allowlisted, not
    inferred. Any base branch outside ``config.allowed_base_branches`` is
    denied with ``POLICY_DENIED_BASE_BRANCH`` before any network mutation.
    """
    if base_branch in config.allowed_base_branches:
        return
    _deny(
        GITHUB_POLICY_DENIED_BASE_BRANCH,
        "GitHub open_pr base branch is not in the allowed base-branch list.",
        base_branch=base_branch,
        allowed_base_branches=list(config.allowed_base_branches),
    )


def ensure_pr_head_allowed(
    *,
    head_ref: str,
    config: GithubToolProfileConfig,
) -> None:
    """Per RWPRS §5.3: ``github.post_pr_review`` and ``github.post_pr_comment``"""
    if any(head_ref.startswith(prefix) for prefix in config.allowed_branch_prefixes):
        return
    _deny(
        GITHUB_POLICY_DENIED_PR_HEAD,
        "GitHub write target PR head ref is outside the allowed smoke prefixes.",
        head_ref=head_ref,
        allowed_branch_prefixes=list(config.allowed_branch_prefixes),
    )


def ensure_paths_allowed(
    *,
    paths: Iterable[str],
    config: GithubToolProfileConfig,
) -> None:
    for path in (str(item or "").strip() for item in paths):
        if any(path.startswith(prefix) for prefix in config.allowed_path_prefixes):
            continue
        _deny(
            GITHUB_POLICY_DENIED_PATH_PREFIX,
            "GitHub write path is outside the allowed smoke prefixes.",
            path=path,
            allowed_path_prefixes=list(config.allowed_path_prefixes),
        )


def ensure_force_push_allowed(
    *,
    force: bool,
    config: GithubToolProfileConfig,
) -> None:
    if not force or config.allow_force_push:
        return
    _deny(
        GITHUB_POLICY_DENIED_FORCE_PUSH,
        "Force-push semantics are not allowed in L3.",
        force=True,
    )


def ensure_merge_allowed(
    *,
    requested: bool,
    config: GithubToolProfileConfig,
    event: str = "",
) -> None:
    if not requested or config.allow_merge:
        return
    _deny(
        GITHUB_POLICY_DENIED_MERGE,
        "Merge-like GitHub write actions are out of scope for L3.",
        event=event,
    )


def ensure_delete_allowed(
    *,
    requested: bool,
    config: GithubToolProfileConfig,
) -> None:
    if not requested or config.allow_delete_branch:
        return
    _deny(
        GITHUB_POLICY_DENIED_DELETE,
        "Delete-like GitHub write actions are out of scope for L3.",
        delete_requested=True,
    )


__all__ = [
    "github_write_policy_from_context",
    "ensure_repository_allowed",
    "ensure_branch_allowed",
    "ensure_base_branch_allowed",
    "ensure_pr_head_allowed",
    "ensure_paths_allowed",
    "ensure_force_push_allowed",
    "ensure_merge_allowed",
    "ensure_delete_allowed",
]
