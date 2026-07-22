from collections.abc import Mapping
from typing import Any, Callable

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.registry.catalog import ToolSpec

from .interfaces import (
    TOOL_GITHUB_COMMIT_FILES,
    TOOL_GITHUB_FETCH_CHECKS,
    TOOL_GITHUB_FETCH_COMMENTS,
    TOOL_GITHUB_FETCH_DIFF,
    TOOL_GITHUB_FETCH_PR,
    TOOL_GITHUB_LIST_PRS,
    TOOL_GITHUB_OPEN_PR,
    TOOL_GITHUB_POST_PR_COMMENT,
    TOOL_GITHUB_POST_PR_REVIEW,
)
from .providers import GithubProvider, provider_registry
from .schemas import (
    GithubCommitFilesArgs,
    GithubFetchChecksArgs,
    GithubFetchCommentsArgs,
    GithubFetchDiffArgs,
    GithubFetchPrArgs,
    GithubListPrsArgs,
    GithubOpenPrArgs,
    GithubPostPrCommentArgs,
    GithubPostPrReviewArgs,
)


_PROVIDER_RESOLVERS: dict[str, str] = {
    TOOL_GITHUB_LIST_PRS: "list_prs",
    TOOL_GITHUB_FETCH_PR: "fetch_pr",
    TOOL_GITHUB_FETCH_DIFF: "fetch_diff",
    TOOL_GITHUB_FETCH_COMMENTS: "fetch_comments",
    TOOL_GITHUB_FETCH_CHECKS: "fetch_checks",
    TOOL_GITHUB_COMMIT_FILES: "commit_files",
    TOOL_GITHUB_OPEN_PR: "open_pr",
    TOOL_GITHUB_POST_PR_REVIEW: "post_pr_review",
    TOOL_GITHUB_POST_PR_COMMENT: "post_pr_comment",
}


def _resolve_provider() -> GithubProvider:
    provider = provider_registry().default()
    if provider is None:
        raise ToolRuntimeError(
            "DEPENDENCY_UNAVAILABLE",
            "No GitHub provider is registered. Register a provider via "
            "`openminion.tools.github.register_provider(...)` before "
            "invoking github.* tools.",
            {"reason_code": "github_provider_unregistered"},
        )
    return provider


def _dispatch(
    tool_name: str,
    args: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    method_name = _PROVIDER_RESOLVERS[tool_name]
    provider = _resolve_provider()
    method: Callable[..., Mapping[str, Any]] = getattr(provider, method_name)
    result = method(args=dict(args), ctx=ctx)
    if not isinstance(result, Mapping):
        raise ToolRuntimeError(
            "PROVIDER_PROTOCOL_VIOLATION",
            f"GitHub provider returned non-mapping for {tool_name!r}",
            {"reason_code": "github_provider_bad_result"},
        )
    return dict(result)


def _h_list_prs(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_LIST_PRS, args, ctx)


def _h_fetch_pr(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_FETCH_PR, args, ctx)


def _h_fetch_diff(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_FETCH_DIFF, args, ctx)


def _h_fetch_comments(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_FETCH_COMMENTS, args, ctx)


def _h_fetch_checks(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_FETCH_CHECKS, args, ctx)


def _h_commit_files(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_COMMIT_FILES, args, ctx)


def _h_open_pr(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_OPEN_PR, args, ctx)


def _h_post_pr_review(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_POST_PR_REVIEW, args, ctx)


def _h_post_pr_comment(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_POST_PR_COMMENT, args, ctx)


def register(registry: ToolRegistry) -> None:
    for spec in _github_tool_specs():
        registry.add(spec)


def _github_tool_specs() -> tuple[ToolSpec, ...]:
    return (
        _github_tool_spec(TOOL_GITHUB_LIST_PRS, GithubListPrsArgs, _h_list_prs, read_only=True),
        _github_tool_spec(TOOL_GITHUB_FETCH_PR, GithubFetchPrArgs, _h_fetch_pr, read_only=True),
        _github_tool_spec(TOOL_GITHUB_FETCH_DIFF, GithubFetchDiffArgs, _h_fetch_diff, read_only=True),
        _github_tool_spec(TOOL_GITHUB_FETCH_COMMENTS, GithubFetchCommentsArgs, _h_fetch_comments, read_only=True),
        _github_tool_spec(TOOL_GITHUB_FETCH_CHECKS, GithubFetchChecksArgs, _h_fetch_checks, read_only=True),
        _github_tool_spec(TOOL_GITHUB_COMMIT_FILES, GithubCommitFilesArgs, _h_commit_files, read_only=False),
        _github_tool_spec(TOOL_GITHUB_OPEN_PR, GithubOpenPrArgs, _h_open_pr, read_only=False),
        _github_tool_spec(TOOL_GITHUB_POST_PR_REVIEW, GithubPostPrReviewArgs, _h_post_pr_review, read_only=False),
        _github_tool_spec(TOOL_GITHUB_POST_PR_COMMENT, GithubPostPrCommentArgs, _h_post_pr_comment, read_only=False),
    )


def _github_tool_spec(
    name: str,
    args_model: type[Any],
    handler: Any,
    *,
    read_only: bool,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        args_model=args_model,
        min_scope="READ_ONLY" if read_only else "WRITE_SAFE",
        handler=handler,
        dangerous=not read_only,
        idempotent=read_only,
        tags=("plugin", "github"),
        capabilities=("read_only" if read_only else "write_safe", "network"),
    )



__all__ = ["register"]
