from __future__ import annotations

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.github.config import GithubToolProfileConfig
from openminion.tools.github.policy import (
    ensure_base_branch_allowed,
    ensure_branch_allowed,
    ensure_delete_allowed,
    ensure_force_push_allowed,
    ensure_merge_allowed,
    ensure_paths_allowed,
    ensure_pr_head_allowed,
    ensure_repository_allowed,
)


def _default_config() -> GithubToolProfileConfig:
    return GithubToolProfileConfig()


def test_repository_allowlist_accepts_default_smoke_repo() -> None:
    ensure_repository_allowed(
        owner="openminion",
        repo="test-repo-for-agent",
        config=_default_config(),
    )


def test_repository_allowlist_denies_mismatch() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_repository_allowed(
            owner="evil",
            repo="other-repo",
            config=_default_config(),
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_REPO"


def test_branch_policy_allows_smoke_branch() -> None:
    ensure_branch_allowed(
        branch="openminion-smoke/run-123",
        base_branch="main",
        config=_default_config(),
    )


def test_branch_policy_denies_default_branch() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_branch_allowed(
            branch="main",
            base_branch="main",
            config=_default_config(),
        )

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_DEFAULT_BRANCH"


def test_branch_policy_denies_prefix_escape() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_branch_allowed(
            branch="feature/not-allowed",
            base_branch="main",
            config=_default_config(),
        )

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_BRANCH_PREFIX"


def test_path_policy_allows_smoke_prefix() -> None:
    ensure_paths_allowed(
        paths=[".openminion-smoke/run-123.md"],
        config=_default_config(),
    )


def test_path_policy_denies_prefix_escape() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_paths_allowed(
            paths=["README.md"],
            config=_default_config(),
        )

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_PATH_PREFIX"


def test_force_push_policy_denies_true() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_force_push_allowed(force=True, config=_default_config())

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_FORCE_PUSH"


def test_force_push_policy_allows_false() -> None:
    ensure_force_push_allowed(force=False, config=_default_config())


def test_merge_policy_denies_merge_like_request() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_merge_allowed(
            requested=True,
            config=_default_config(),
            event="APPROVE",
        )

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_MERGE"


def test_merge_policy_allows_no_merge_request() -> None:
    ensure_merge_allowed(
        requested=False,
        config=_default_config(),
        event="COMMENT",
    )


def test_delete_policy_denies_delete_like_request() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_delete_allowed(requested=True, config=_default_config())

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_DELETE"


def test_delete_policy_allows_non_delete_request() -> None:
    ensure_delete_allowed(requested=False, config=_default_config())


def test_base_branch_policy_allows_configured_default() -> None:
    ensure_base_branch_allowed(base_branch="main", config=_default_config())


def test_base_branch_policy_denies_unallowed_base() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_base_branch_allowed(
            base_branch="release/v1",
            config=_default_config(),
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_BASE_BRANCH"
    assert exc.value.details.get("base_branch") == "release/v1"


def test_base_branch_policy_respects_profile_override() -> None:
    config = GithubToolProfileConfig.from_mapping(
        {"allowed_base_branches": ["release/stable"]}
    )
    ensure_base_branch_allowed(base_branch="release/stable", config=config)
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_base_branch_allowed(base_branch="main", config=config)
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_BASE_BRANCH"


def test_pr_head_policy_allows_smoke_prefix() -> None:
    ensure_pr_head_allowed(
        head_ref="openminion-smoke/pr-7",
        config=_default_config(),
    )


def test_pr_head_policy_denies_non_smoke_head() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        ensure_pr_head_allowed(
            head_ref="feature/random",
            config=_default_config(),
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_PR_HEAD"
    assert exc.value.details.get("head_ref") == "feature/random"
