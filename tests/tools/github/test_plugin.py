from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.github.constants import DEFAULT_GITHUB_PROVIDER_ID
from openminion.tools.github.interfaces import (
    TOOL_GITHUB_COMMIT_FILES,
    TOOL_GITHUB_FETCH_CHECKS,
    TOOL_GITHUB_FETCH_COMMENTS,
    TOOL_GITHUB_FETCH_DIFF,
    TOOL_GITHUB_FETCH_PR,
    TOOL_GITHUB_LIST_PRS,
    TOOL_GITHUB_OPEN_PR,
    TOOL_GITHUB_UPDATE_PR,
    TOOL_GITHUB_POST_PR_COMMENT,
    TOOL_GITHUB_POST_PR_REVIEW,
)
from openminion.tools.github.plugin import read_update_pr, register
from openminion.tools.github.providers import (
    provider_registry,
    register_provider,
)


class _StubProvider:
    provider_id = DEFAULT_GITHUB_PROVIDER_ID

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, method: str, args: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(args)
        self.calls.append((method, payload))
        return {
            "ok": True,
            "data": {"method": method, "args": payload},
        }

    def list_prs(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("list_prs", args)

    def fetch_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("fetch_pr", args)

    def fetch_diff(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("fetch_diff", args)

    def fetch_comments(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("fetch_comments", args)

    def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("fetch_checks", args)

    def commit_files(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("commit_files", args)

    def open_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("open_pr", args)

    def update_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("update_pr", args)

    def read_update_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("read_update_pr", args)

    def post_pr_review(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("post_pr_review", args)

    def post_pr_comment(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return self._record("post_pr_comment", args)

    def healthcheck(self) -> bool:
        return True


@pytest.fixture
def registry_with_tools() -> ToolRegistry:
    registry = ToolRegistry()
    register(registry)
    return registry


@pytest.fixture
def stub_provider() -> _StubProvider:
    provider_registry().reset()
    provider = _StubProvider()
    register_provider(provider)
    yield provider
    provider_registry().reset()


def test_register_adds_all_ten_tools(registry_with_tools: ToolRegistry) -> None:
    expected = {
        TOOL_GITHUB_LIST_PRS,
        TOOL_GITHUB_FETCH_PR,
        TOOL_GITHUB_FETCH_DIFF,
        TOOL_GITHUB_FETCH_COMMENTS,
        TOOL_GITHUB_FETCH_CHECKS,
        TOOL_GITHUB_COMMIT_FILES,
        TOOL_GITHUB_OPEN_PR,
        TOOL_GITHUB_UPDATE_PR,
        TOOL_GITHUB_POST_PR_REVIEW,
        TOOL_GITHUB_POST_PR_COMMENT,
    }
    listed = set(registry_with_tools.list().keys())
    assert expected.issubset(listed)


def test_each_tool_is_read_only_and_idempotent(
    registry_with_tools: ToolRegistry,
) -> None:
    for name in (
        TOOL_GITHUB_LIST_PRS,
        TOOL_GITHUB_FETCH_PR,
        TOOL_GITHUB_FETCH_DIFF,
        TOOL_GITHUB_FETCH_COMMENTS,
        TOOL_GITHUB_FETCH_CHECKS,
    ):
        spec = registry_with_tools.list()[name]
        assert spec.min_scope == "READ_ONLY", f"{name} scope drifted"
        assert spec.dangerous is False, f"{name} should not be dangerous"
        assert spec.idempotent is True, f"{name} should be idempotent"
        assert "read_only" in spec.capabilities
        assert "github" in spec.tags


def test_each_write_tool_is_write_safe_and_non_idempotent(
    registry_with_tools: ToolRegistry,
) -> None:
    for name in (
        TOOL_GITHUB_COMMIT_FILES,
        TOOL_GITHUB_OPEN_PR,
        TOOL_GITHUB_UPDATE_PR,
        TOOL_GITHUB_POST_PR_REVIEW,
        TOOL_GITHUB_POST_PR_COMMENT,
    ):
        spec = registry_with_tools.list()[name]
        assert spec.min_scope == "WRITE_SAFE", f"{name} scope drifted"
        assert spec.dangerous is True, f"{name} should be dangerous"
        assert spec.idempotent is False, f"{name} should be non-idempotent"
        assert "write_safe" in spec.capabilities
        assert "github" in spec.tags


def test_list_prs_dispatches_to_provider(
    registry_with_tools: ToolRegistry, stub_provider: _StubProvider
) -> None:
    spec = registry_with_tools.list()[TOOL_GITHUB_LIST_PRS]
    args = {"owner": "octocat", "repo": "hello-world", "state": "open", "limit": 5}
    result = spec.handler(args, ctx=None)
    assert result["ok"] is True
    assert result["data"]["method"] == "list_prs"
    assert stub_provider.calls == [("list_prs", args)]


def test_fetch_pr_dispatches_to_provider(
    registry_with_tools: ToolRegistry, stub_provider: _StubProvider
) -> None:
    spec = registry_with_tools.list()[TOOL_GITHUB_FETCH_PR]
    args = {"owner": "octocat", "repo": "hello-world", "number": 42}
    result = spec.handler(args, ctx=None)
    assert result["ok"] is True
    assert result["data"]["method"] == "fetch_pr"
    assert stub_provider.calls == [("fetch_pr", args)]


def test_commit_files_dispatches_to_provider(
    registry_with_tools: ToolRegistry, stub_provider: _StubProvider
) -> None:
    spec = registry_with_tools.list()[TOOL_GITHUB_COMMIT_FILES]
    args = {
        "owner": "openminion",
        "repo": "test-repo-for-agent",
        "branch": "openminion-smoke/abc",
        "base_branch": "main",
        "message": "smoke",
        "files": [{"path": ".openminion-smoke/abc.md", "content": "hello\n"}],
    }
    result = spec.handler(args, ctx=None)
    assert result["ok"] is True
    assert result["data"]["method"] == "commit_files"
    assert stub_provider.calls == [("commit_files", args)]


def test_update_pr_dispatches_to_provider(
    registry_with_tools: ToolRegistry, stub_provider: _StubProvider
) -> None:
    spec = registry_with_tools.list()[TOOL_GITHUB_UPDATE_PR]
    args = {
        "owner": "openminion",
        "repo": "test-repo-for-agent",
        "number": 17,
        "title": "Updated title",
        "body": None,
    }
    result = spec.handler(args, ctx=None)
    assert result["data"]["method"] == "update_pr"
    assert stub_provider.calls == [("update_pr", args)]


def test_no_provider_raises_dependency_unavailable(
    registry_with_tools: ToolRegistry,
) -> None:
    provider_registry().reset()
    spec = registry_with_tools.list()[TOOL_GITHUB_LIST_PRS]
    with pytest.raises(ToolRuntimeError) as exc:
        spec.handler({"owner": "o", "repo": "r"}, ctx=None)
    assert exc.value.code == "DEPENDENCY_UNAVAILABLE"
    assert exc.value.details.get("reason_code") == "github_provider_unregistered"


def test_args_schema_rejects_malformed_owner(
    registry_with_tools: ToolRegistry, stub_provider: _StubProvider
) -> None:
    from openminion.tools.github.schemas import GithubListPrsArgs

    with pytest.raises(Exception):
        GithubListPrsArgs.model_validate({"owner": "evil/path", "repo": "r"})

    with pytest.raises(Exception):
        GithubListPrsArgs.model_validate({"owner": "", "repo": "r"})

    with pytest.raises(Exception):
        GithubListPrsArgs.model_validate({"owner": "o", "repo": "r", "state": "weird"})


def test_fetch_diff_default_max_lines() -> None:
    from openminion.tools.github.schemas import GithubFetchDiffArgs

    args = GithubFetchDiffArgs.model_validate({"owner": "o", "repo": "r", "number": 1})
    assert args.max_lines >= 100  # bounded default applied


def test_fetch_checks_rejects_non_hex_sha() -> None:
    from openminion.tools.github.schemas import GithubFetchChecksArgs

    with pytest.raises(Exception):
        GithubFetchChecksArgs.model_validate(
            {"owner": "o", "repo": "r", "head_sha": "not-a-sha"}
        )

    parsed = GithubFetchChecksArgs.model_validate(
        {
            "owner": "o",
            "repo": "r",
            "head_sha": "ABC1234",
            "expected_checks": [" lint ", "test (3.11)"],
        }
    )
    assert parsed.head_sha == "abc1234"
    assert parsed.expected_checks == ["lint", "test (3.11)"]

    with pytest.raises(Exception):
        GithubFetchChecksArgs.model_validate(
            {
                "owner": "o",
                "repo": "r",
                "head_sha": "abc1234",
                "expected_checks": ["lint", "lint"],
            }
        )


def test_update_pr_schema_requires_title_or_body() -> None:
    from openminion.tools.github.schemas import GithubUpdatePrArgs

    with pytest.raises(Exception):
        GithubUpdatePrArgs.model_validate({"owner": "o", "repo": "r", "number": 1})
    with pytest.raises(Exception):
        GithubUpdatePrArgs.model_validate(
            {"owner": "o", "repo": "r", "number": 1, "title": ""}
        )
    parsed = GithubUpdatePrArgs.model_validate(
        {"owner": "o", "repo": "r", "number": 1, "title": " New title "}
    )
    assert parsed.title == "New title"
    assert parsed.body is None


def test_fetch_checks_dispatches_expected_names(
    registry_with_tools: ToolRegistry, stub_provider: _StubProvider
) -> None:
    spec = registry_with_tools.list()[TOOL_GITHUB_FETCH_CHECKS]
    args = {
        "owner": "octocat",
        "repo": "hello-world",
        "head_sha": "abc1234",
        "expected_checks": ["lint", "test (3.11)"],
    }

    result = spec.handler(args, ctx=None)

    assert result["ok"] is True
    assert result["data"]["method"] == "fetch_checks"
    assert stub_provider.calls == [("fetch_checks", args)]


def test_fetch_checks_runtime_invocation_validates_and_dispatches(
    registry_with_tools: ToolRegistry,
    stub_provider: _StubProvider,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    result = execute_tool_spec_call(
        tool=registry_with_tools.list()[TOOL_GITHUB_FETCH_CHECKS],
        arguments={
            "owner": "octocat",
            "repo": "hello-world",
            "head_sha": "ABC1234",
            "expected_checks": ["lint"],
        },
        context=ToolExecutionContext(
            channel="test",
            target="local",
            session_id="github-checks-runtime",
            metadata={"workspace_root": str(tmp_path)},
        ),
    )

    assert result.ok is True
    assert stub_provider.calls == [
        (
            "fetch_checks",
            {
                "owner": "octocat",
                "repo": "hello-world",
                "head_sha": "abc1234",
                "expected_checks": ["lint"],
            },
        )
    ]


def test_commit_files_schema_rejects_path_escape() -> None:
    from openminion.tools.github.schemas import GithubCommitFilesArgs

    with pytest.raises(Exception):
        GithubCommitFilesArgs.model_validate(
            {
                "owner": "o",
                "repo": "r",
                "branch": "openminion-smoke/x",
                "base_branch": "main",
                "message": "smoke",
                "files": [{"path": "../escape.txt", "content": "x"}],
            }
        )


def test_open_pr_schema_requires_body() -> None:
    from openminion.tools.github.schemas import GithubOpenPrArgs

    with pytest.raises(Exception):
        GithubOpenPrArgs.model_validate(
            {"owner": "o", "repo": "r", "head": "h", "base": "main", "title": "t"}
        )


def test_post_pr_review_schema_rejects_non_comment_events() -> None:
    from openminion.tools.github.schemas import GithubPostPrReviewArgs

    with pytest.raises(Exception):
        GithubPostPrReviewArgs.model_validate(
            {"owner": "o", "repo": "r", "number": 1, "event": "approve", "body": "x"}
        )

    parsed = GithubPostPrReviewArgs.model_validate(
        {"owner": "o", "repo": "r", "number": 1, "event": "comment", "body": "x"}
    )
    assert parsed.event == "COMMENT"


def test_post_pr_comment_schema_requires_body() -> None:
    from openminion.tools.github.schemas import GithubPostPrCommentArgs

    with pytest.raises(Exception):
        GithubPostPrCommentArgs.model_validate(
            {"owner": "o", "repo": "r", "number": 1, "body": ""}
        )


def test_invalid_provider_result_raises_public_error(
    registry_with_tools: ToolRegistry,
) -> None:
    class _BadProvider:
        provider_id = DEFAULT_GITHUB_PROVIDER_ID

        def list_prs(self, *, args: Any, ctx: Any) -> Any:
            return "not-a-mapping"

        def fetch_pr(self, **kw): ...
        def fetch_diff(self, **kw): ...
        def fetch_comments(self, **kw): ...
        def fetch_checks(self, **kw): ...
        def commit_files(self, **kw): ...
        def open_pr(self, **kw): ...
        def post_pr_review(self, **kw): ...
        def post_pr_comment(self, **kw): ...
        def healthcheck(self) -> bool:
            return True

    provider_registry().reset()
    register_provider(_BadProvider())  # type: ignore[arg-type]
    try:
        spec = registry_with_tools.list()[TOOL_GITHUB_LIST_PRS]
        with pytest.raises(ToolRuntimeError) as exc:
            spec.handler({"owner": "o", "repo": "r"}, ctx=None)
        assert exc.value.code == "INVALID_RESPONSE"
        assert exc.value.details["reason_code"] == "github_provider_bad_result"
    finally:
        provider_registry().reset()


def test_update_pr_rejects_invalid_provider_results(
    monkeypatch: pytest.MonkeyPatch,
    registry_with_tools: ToolRegistry,
    stub_provider: _StubProvider,
) -> None:
    def bad_result(**kwargs: Any) -> Any:
        del kwargs
        return "not-a-mapping"

    monkeypatch.setattr(stub_provider, "update_pr", bad_result)
    spec = registry_with_tools.list()[TOOL_GITHUB_UPDATE_PR]
    with pytest.raises(ToolRuntimeError) as invoke_error:
        spec.handler(
            {"owner": "o", "repo": "r", "number": 1, "title": "Updated"},
            ctx=None,
        )
    assert invoke_error.value.code == "INVALID_RESPONSE"
    assert invoke_error.value.details["reason_code"] == "github_provider_bad_result"

    monkeypatch.setattr(stub_provider, "read_update_pr", bad_result)
    with pytest.raises(ToolRuntimeError) as preflight_error:
        read_update_pr({"owner": "o", "repo": "r", "number": 1}, ctx=None)
    assert preflight_error.value.code == "INVALID_RESPONSE"
    assert preflight_error.value.details["reason_code"] == "github_provider_bad_result"
