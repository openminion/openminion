from __future__ import annotations

import io
import json
from typing import Any
from urllib.error import HTTPError

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.github.rest import GithubRestProvider


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


def test_list_prs_maps_rest_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["auth"] = request.headers.get("Authorization")
        captured["timeout"] = timeout
        return _FakeResponse(
            json.dumps(
                [
                    {
                        "number": 7,
                        "title": "Test PR",
                        "user": {"login": "alice"},
                        "head": {"sha": "abc1234", "ref": "feature"},
                        "base": {"ref": "main"},
                        "draft": False,
                        "labels": [{"name": "bug"}],
                        "comments": 1,
                        "review_comments": 2,
                        "html_url": "https://github.com/o/r/pull/7",
                    }
                ]
            )
        )

    monkeypatch.setattr("openminion.tools.github.rest.urlopen", fake_urlopen)

    provider = GithubRestProvider()
    result = provider.list_prs(
        args={"owner": "o", "repo": "r", "state": "open", "limit": 5},
        ctx=None,
    )

    assert result["ok"] is True
    assert (
        captured["url"]
        == "https://api.github.com/repos/o/r/pulls?state=open&per_page=5"
    )
    assert captured["auth"] == "Bearer test-token"
    assert captured["timeout"] == 30.0
    pr = result["data"]["open_prs"][0]
    assert pr["number"] == 7
    assert pr["head_sha"] == "abc1234"
    assert pr["labels"] == ["bug"]
    assert pr["comments_count"] == 3


def test_fetch_diff_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        del request, timeout
        return _FakeResponse("a\nb\nc\n")

    monkeypatch.setattr("openminion.tools.github.rest.urlopen", fake_urlopen)

    provider = GithubRestProvider()
    result = provider.fetch_diff(
        args={"owner": "o", "repo": "r", "number": 1, "max_lines": 2},
        ctx=None,
    )

    assert result["ok"] is True
    assert result["data"]["diff"] == "a\nb"
    assert result["data"]["truncated"] is True
    assert result["data"]["line_count"] == 3


def test_auth_invalid_maps_401(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        del timeout
        raise HTTPError(
            url=request.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"Bad credentials"}'),
        )

    monkeypatch.setattr("openminion.tools.github.rest.urlopen", fake_urlopen)

    provider = GithubRestProvider()
    with pytest.raises(ToolRuntimeError) as exc:
        provider.list_prs(args={"owner": "o", "repo": "r"}, ctx=None)

    assert exc.value.code == "AUTH_INVALID"
    assert exc.value.details.get("reason_code") == "github_pat_invalid"
    assert exc.value.details.get("status_code") == 401


def test_commit_files_policy_denied_skips_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    call_count = 0

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        nonlocal call_count
        del request, timeout
        call_count += 1
        return _FakeResponse("{}")

    monkeypatch.setattr("openminion.tools.github.rest.urlopen", fake_urlopen)

    provider = GithubRestProvider()
    with pytest.raises(ToolRuntimeError) as exc:
        provider.commit_files(
            args={
                "owner": "openminion",
                "repo": "test-repo-for-agent",
                "branch": "main",
                "base_branch": "main",
                "message": "smoke",
                "files": [{"path": ".openminion-smoke/test.md", "content": "x"}],
            },
            ctx=None,
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_DEFAULT_BRANCH"
    assert call_count == 0


def test_open_pr_policy_denied_skips_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    call_count = 0

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        nonlocal call_count
        del request, timeout
        call_count += 1
        return _FakeResponse("{}")

    monkeypatch.setattr("openminion.tools.github.rest.urlopen", fake_urlopen)

    provider = GithubRestProvider()
    with pytest.raises(ToolRuntimeError) as exc:
        provider.open_pr(
            args={
                "owner": "evil",
                "repo": "other-repo",
                "head": "openminion-smoke/x",
                "base": "main",
                "title": "smoke",
                "body": "body",
            },
            ctx=None,
        )

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_REPO"
    assert call_count == 0


def test_post_pr_review_non_comment_denied_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    call_count = 0

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        nonlocal call_count
        del request, timeout
        call_count += 1
        return _FakeResponse("{}")

    monkeypatch.setattr("openminion.tools.github.rest.urlopen", fake_urlopen)

    provider = GithubRestProvider()
    with pytest.raises(ToolRuntimeError) as exc:
        provider.post_pr_review(
            args={
                "owner": "openminion",
                "repo": "test-repo-for-agent",
                "number": 1,
                "event": "APPROVE",
                "body": "nice",
            },
            ctx=None,
        )

    assert exc.value.details.get("reason_code") == "POLICY_DENIED_MERGE"
    assert call_count == 0


def test_commit_files_maps_git_data_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    requests: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = iter(
        [
            {"ref": "refs/heads/main", "object": {"sha": "base-sha"}},
            None,
            {"tree": {"sha": "base-tree"}},
            {"sha": "new-tree"},
            {"sha": "new-commit"},
            {
                "ref": "refs/heads/openminion-smoke/test",
                "object": {"sha": "new-commit"},
            },
            {
                "ref": "refs/heads/openminion-smoke/test",
                "object": {"sha": "new-commit"},
            },
        ]
    )

    def fake_request_json(
        self: GithubRestProvider,
        *,
        ctx: Any,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        del ctx, query
        requests.append((method, path, body))
        return next(responses)

    def fake_request_json_or_none_on_404(
        self: GithubRestProvider,
        *,
        ctx: Any,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        del ctx, query, method, body
        requests.append(("GET", path, None))
        return None

    monkeypatch.setattr(GithubRestProvider, "_request_json", fake_request_json)
    monkeypatch.setattr(
        GithubRestProvider,
        "_request_json_or_none_on_404",
        fake_request_json_or_none_on_404,
    )

    provider = GithubRestProvider()
    result = provider.commit_files(
        args={
            "owner": "openminion",
            "repo": "test-repo-for-agent",
            "branch": "openminion-smoke/test",
            "base_branch": "main",
            "message": "smoke commit",
            "files": [{"path": ".openminion-smoke/test.md", "content": "hello\n"}],
        },
        ctx=None,
    )

    assert result["ok"] is True
    assert result["data"]["commit_sha"] == "new-commit"
    assert requests[0] == (
        "GET",
        "/repos/openminion/test-repo-for-agent/git/ref/heads/main",
        None,
    )
    assert requests[1] == (
        "GET",
        "/repos/openminion/test-repo-for-agent/git/ref/heads/openminion-smoke/test",
        None,
    )
    assert requests[2][0] == "POST"
    assert requests[2][1] == "/repos/openminion/test-repo-for-agent/git/refs"
    assert requests[2][2] == {
        "ref": "refs/heads/openminion-smoke/test",
        "sha": "base-sha",
    }
    assert requests[4][0] == "POST"
    assert requests[4][1] == "/repos/openminion/test-repo-for-agent/git/trees"
    assert requests[5][1] == "/repos/openminion/test-repo-for-agent/git/commits"
    assert requests[6] == (
        "PATCH",
        "/repos/openminion/test-repo-for-agent/git/refs/heads/openminion-smoke/test",
        {"sha": "new-commit", "force": False},
    )


def test_open_pr_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    captured: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_request_json(
        self: GithubRestProvider,
        *,
        ctx: Any,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        del self, ctx, query
        captured.append((method, path, body))
        return {
            "number": 12,
            "html_url": "https://github.com/o/r/pull/12",
            "state": "open",
        }

    monkeypatch.setattr(GithubRestProvider, "_request_json", fake_request_json)

    provider = GithubRestProvider()
    result = provider.open_pr(
        args={
            "owner": "openminion",
            "repo": "test-repo-for-agent",
            "head": "openminion-smoke/test",
            "base": "main",
            "title": "Smoke PR",
            "body": "Created by smoke test.",
        },
        ctx=None,
    )

    assert result["data"]["number"] == 12
    assert captured == [
        (
            "POST",
            "/repos/openminion/test-repo-for-agent/pulls",
            {
                "title": "Smoke PR",
                "body": "Created by smoke test.",
                "head": "openminion-smoke/test",
                "base": "main",
            },
        )
    ]


def test_post_comment_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    captured: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = iter(
        [
            {"head": {"ref": "openminion-smoke/pr-1"}},
            {
                "id": 77,
                "html_url": "https://github.com/o/r/issues/1#issuecomment-77",
                "body": "hello",
            },
        ]
    )

    def fake_request_json(
        self: GithubRestProvider,
        *,
        ctx: Any,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        del self, ctx, query
        captured.append((method, path, body))
        return next(responses)

    monkeypatch.setattr(GithubRestProvider, "_request_json", fake_request_json)

    provider = GithubRestProvider()
    result = provider.post_pr_comment(
        args={
            "owner": "openminion",
            "repo": "test-repo-for-agent",
            "number": 1,
            "body": "hello",
        },
        ctx=None,
    )

    assert result["data"]["id"] == 77
    assert captured == [
        ("GET", "/repos/openminion/test-repo-for-agent/pulls/1", None),
        (
            "POST",
            "/repos/openminion/test-repo-for-agent/issues/1/comments",
            {"body": "hello"},
        ),
    ]


def test_post_review_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    captured: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = iter(
        [
            {"head": {"ref": "openminion-smoke/pr-1"}},
            {
                "id": 88,
                "html_url": "https://github.com/o/r/pull/1#pullrequestreview-88",
                "body": "hello",
                "state": "COMMENTED",
            },
        ]
    )

    def fake_request_json(
        self: GithubRestProvider,
        *,
        ctx: Any,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        del self, ctx, query
        captured.append((method, path, body))
        return next(responses)

    monkeypatch.setattr(GithubRestProvider, "_request_json", fake_request_json)

    provider = GithubRestProvider()
    result = provider.post_pr_review(
        args={
            "owner": "openminion",
            "repo": "test-repo-for-agent",
            "number": 1,
            "event": "COMMENT",
            "body": "hello",
        },
        ctx=None,
    )

    assert result["data"]["id"] == 88
    assert captured == [
        ("GET", "/repos/openminion/test-repo-for-agent/pulls/1", None),
        (
            "POST",
            "/repos/openminion/test-repo-for-agent/pulls/1/reviews",
            {"event": "COMMENT", "body": "hello"},
        ),
    ]


def test_open_pr_denies_disallowed_base_branch_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    call_count = 0

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        nonlocal call_count
        del request, timeout
        call_count += 1
        return _FakeResponse("{}")

    monkeypatch.setattr("openminion.tools.github.rest.urlopen", fake_urlopen)

    provider = GithubRestProvider()
    with pytest.raises(ToolRuntimeError) as exc:
        provider.open_pr(
            args={
                "owner": "openminion",
                "repo": "test-repo-for-agent",
                "head": "openminion-smoke/x",
                "base": "release/v1",
                "title": "smoke",
                "body": "body",
            },
            ctx=None,
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_BASE_BRANCH"
    assert exc.value.details.get("base_branch") == "release/v1"
    assert call_count == 0


def test_post_pr_review_denies_disallowed_pr_head_after_read_no_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    captured: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = iter([{"head": {"ref": "feature/not-smoke"}}])

    def fake_request_json(
        self: GithubRestProvider,
        *,
        ctx: Any,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        del self, ctx, query
        captured.append((method, path, body))
        return next(responses)

    monkeypatch.setattr(GithubRestProvider, "_request_json", fake_request_json)

    provider = GithubRestProvider()
    with pytest.raises(ToolRuntimeError) as exc:
        provider.post_pr_review(
            args={
                "owner": "openminion",
                "repo": "test-repo-for-agent",
                "number": 42,
                "event": "COMMENT",
                "body": "hi",
            },
            ctx=None,
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_PR_HEAD"
    assert exc.value.details.get("head_ref") == "feature/not-smoke"
    # Exactly one GET (the head-ref lookup), no POST mutation.
    assert captured == [
        ("GET", "/repos/openminion/test-repo-for-agent/pulls/42", None),
    ]


def test_post_pr_comment_denies_disallowed_pr_head_after_read_no_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    captured: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = iter([{"head": {"ref": "feature/not-smoke"}}])

    def fake_request_json(
        self: GithubRestProvider,
        *,
        ctx: Any,
        path: str,
        query: dict[str, str] | None = None,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        del self, ctx, query
        captured.append((method, path, body))
        return next(responses)

    monkeypatch.setattr(GithubRestProvider, "_request_json", fake_request_json)

    provider = GithubRestProvider()
    with pytest.raises(ToolRuntimeError) as exc:
        provider.post_pr_comment(
            args={
                "owner": "openminion",
                "repo": "test-repo-for-agent",
                "number": 42,
                "body": "hi",
            },
            ctx=None,
        )

    assert exc.value.code == "POLICY_DENIED"
    assert exc.value.details.get("reason_code") == "POLICY_DENIED_PR_HEAD"
    assert exc.value.details.get("head_ref") == "feature/not-smoke"
    assert captured == [
        ("GET", "/repos/openminion/test-repo-for-agent/pulls/42", None),
    ]
