from __future__ import annotations

import os
import time

import pytest

from openminion.tools.github.auth import require_github_pat
from openminion.tools.github.rest import GithubRestProvider

pytestmark = pytest.mark.e2e


_LIVE_FLAG = "OPENMINION_LIVE_GITHUB_WRITE_E2E"
_OWNER_ENV = "OPENMINION_LIVE_GITHUB_OWNER"
_REPO_ENV = "OPENMINION_LIVE_GITHUB_REPO"


def _live_enabled() -> bool:
    return os.environ.get(_LIVE_FLAG, "").strip() in {"1", "true", "yes"}


def _has_pat() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN", "").strip())


def _target_owner_repo() -> tuple[str, str]:
    owner = os.environ.get(_OWNER_ENV, "openminion").strip()
    repo = os.environ.get(_REPO_ENV, "test-repo-for-agent").strip()
    return owner, repo


@pytest.mark.skipif(
    not _live_enabled(),
    reason=f"set {_LIVE_FLAG}=1 to enable RWPR-07 live test",
)
@pytest.mark.skipif(
    not _has_pat(),
    reason="GITHUB_TOKEN not set; RWPR-07 live test requires a PAT",
)
def test_rwpr_07_live_github_write_smoke() -> None:
    require_github_pat(env=os.environ)
    owner, repo = _target_owner_repo()
    provider = GithubRestProvider()

    repo_row = provider._request_json(  # noqa: SLF001
        ctx=None,
        path=f"/repos/{owner}/{repo}",
    )
    assert repo_row["full_name"] == f"{owner}/{repo}"
    default_branch = str(repo_row["default_branch"] or "main")
    run_id = str(int(time.time()))
    branch = f"openminion-smoke/{run_id}"
    file_path = f".openminion-smoke/{run_id}.md"

    commit_result = provider.commit_files(
        args={
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "base_branch": default_branch,
            "message": f"OpenMinion smoke commit {run_id}",
            "files": [{"path": file_path, "content": f"smoke {run_id}\n"}],
        },
        ctx=None,
    )
    assert commit_result["ok"] is True
    assert commit_result["data"]["files"] == [file_path]

    pr_result = provider.open_pr(
        args={
            "owner": owner,
            "repo": repo,
            "head": branch,
            "base": default_branch,
            "title": f"OpenMinion smoke PR {run_id}",
            "body": "Created by RWPR-07 live smoke.",
        },
        ctx=None,
    )
    assert pr_result["ok"] is True
    number = int(pr_result["data"]["number"])
    assert number >= 1

    review_result = provider.post_pr_review(
        args={
            "owner": owner,
            "repo": repo,
            "number": number,
            "event": "COMMENT",
            "body": "OpenMinion smoke review comment.",
        },
        ctx=None,
    )
    assert review_result["ok"] is True

    comment_result = provider.post_pr_comment(
        args={
            "owner": owner,
            "repo": repo,
            "number": number,
            "body": "OpenMinion smoke PR thread comment.",
        },
        ctx=None,
    )
    assert comment_result["ok"] is True
