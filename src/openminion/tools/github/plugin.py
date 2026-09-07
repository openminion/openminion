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
    TOOL_GITHUB_UPDATE_PR,
    TOOL_GITHUB_MERGE_PR,
    TOOL_GITHUB_DISPATCH_WORKFLOW,
    TOOL_GITHUB_LIST_WORKFLOW_RUNS,
    TOOL_GITHUB_CREATE_RELEASE,
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
    GithubUpdatePrArgs,
    GithubMergePrArgs,
    GithubDispatchWorkflowArgs,
    GithubListWorkflowRunsArgs,
    GithubCreateReleaseArgs,
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
    TOOL_GITHUB_UPDATE_PR: "update_pr",
    TOOL_GITHUB_MERGE_PR: "merge_pr",
    TOOL_GITHUB_DISPATCH_WORKFLOW: "dispatch_workflow",
    TOOL_GITHUB_LIST_WORKFLOW_RUNS: "list_workflow_runs",
    TOOL_GITHUB_CREATE_RELEASE: "create_release",
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


def resolve_open_pr_head_sha(args: Mapping[str, Any], ctx: Any) -> str:
    return _resolve_provider().resolve_open_pr_head_sha(args=args, ctx=ctx)


def find_open_pr(
    args: Mapping[str, Any],
    ctx: Any,
    *,
    head_sha: str,
) -> dict[str, Any] | None:
    row = _resolve_provider().find_open_pr(args=args, ctx=ctx, head_sha=head_sha)
    return dict(row) if row is not None else None


def read_update_pr(args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    result = _resolve_provider().read_update_pr(args=args, ctx=ctx)
    return _validated_provider_result(TOOL_GITHUB_UPDATE_PR, result)


def read_merge_pr(args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    result = _resolve_provider().read_merge_pr(args=args, ctx=ctx)
    return _validated_provider_result(TOOL_GITHUB_MERGE_PR, result)


def read_dispatch_workflow(args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    result = _resolve_provider().read_dispatch_workflow(args=args, ctx=ctx)
    return _validated_provider_result(TOOL_GITHUB_DISPATCH_WORKFLOW, result)


def read_release(args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    result = _resolve_provider().read_release(args=args, ctx=ctx)
    return _validated_provider_result(TOOL_GITHUB_CREATE_RELEASE, result)


def fetch_merge_checks(args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
    result = _resolve_provider().fetch_checks(
        args={
            "owner": args.get("owner"),
            "repo": args.get("repo"),
            "head_sha": args.get("expected_head_sha"),
            "expected_checks": args.get("expected_checks"),
        },
        ctx=ctx,
    )
    return _validated_provider_result(TOOL_GITHUB_FETCH_CHECKS, result)


def _validated_provider_result(
    tool_name: str,
    result: Any,
) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            f"GitHub provider returned non-mapping for {tool_name!r}",
            {"reason_code": "github_provider_bad_result"},
        )
    return dict(result)


def _dispatch(
    tool_name: str,
    args: dict[str, Any],
    ctx: Any,
) -> dict[str, Any]:
    method_name = _PROVIDER_RESOLVERS[tool_name]
    provider = _resolve_provider()
    method: Callable[..., Mapping[str, Any]] = getattr(provider, method_name)
    result = method(args=dict(args), ctx=ctx)
    return _validated_provider_result(tool_name, result)


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


def _h_update_pr(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_UPDATE_PR, args, ctx)


def _h_merge_pr(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_MERGE_PR, args, ctx)


def _h_dispatch_workflow(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_DISPATCH_WORKFLOW, args, ctx)


def _h_list_workflow_runs(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_LIST_WORKFLOW_RUNS, args, ctx)


def _h_create_release(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_CREATE_RELEASE, args, ctx)


def _h_post_pr_review(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_POST_PR_REVIEW, args, ctx)


def _h_post_pr_comment(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _dispatch(TOOL_GITHUB_POST_PR_COMMENT, args, ctx)


def register(registry: ToolRegistry) -> None:
    for spec in _github_tool_specs():
        registry.add(spec)


def _github_tool_specs() -> tuple[ToolSpec, ...]:
    return (
        _github_tool_spec(
            TOOL_GITHUB_LIST_PRS, GithubListPrsArgs, _h_list_prs, read_only=True
        ),
        _github_tool_spec(
            TOOL_GITHUB_FETCH_PR, GithubFetchPrArgs, _h_fetch_pr, read_only=True
        ),
        _github_tool_spec(
            TOOL_GITHUB_FETCH_DIFF, GithubFetchDiffArgs, _h_fetch_diff, read_only=True
        ),
        _github_tool_spec(
            TOOL_GITHUB_FETCH_COMMENTS,
            GithubFetchCommentsArgs,
            _h_fetch_comments,
            read_only=True,
        ),
        _github_tool_spec(
            TOOL_GITHUB_FETCH_CHECKS,
            GithubFetchChecksArgs,
            _h_fetch_checks,
            read_only=True,
        ),
        _github_tool_spec(
            TOOL_GITHUB_COMMIT_FILES,
            GithubCommitFilesArgs,
            _h_commit_files,
            read_only=False,
        ),
        _github_tool_spec(
            TOOL_GITHUB_OPEN_PR, GithubOpenPrArgs, _h_open_pr, read_only=False
        ),
        _github_tool_spec(
            TOOL_GITHUB_UPDATE_PR,
            GithubUpdatePrArgs,
            _h_update_pr,
            read_only=False,
        ),
        _github_tool_spec(
            TOOL_GITHUB_MERGE_PR,
            GithubMergePrArgs,
            _h_merge_pr,
            read_only=False,
        ),
        _github_tool_spec(
            TOOL_GITHUB_DISPATCH_WORKFLOW,
            GithubDispatchWorkflowArgs,
            _h_dispatch_workflow,
            read_only=False,
        ),
        _github_tool_spec(
            TOOL_GITHUB_LIST_WORKFLOW_RUNS,
            GithubListWorkflowRunsArgs,
            _h_list_workflow_runs,
            read_only=True,
        ),
        _github_tool_spec(
            TOOL_GITHUB_CREATE_RELEASE,
            GithubCreateReleaseArgs,
            _h_create_release,
            read_only=False,
        ),
        _github_tool_spec(
            TOOL_GITHUB_POST_PR_REVIEW,
            GithubPostPrReviewArgs,
            _h_post_pr_review,
            read_only=False,
        ),
        _github_tool_spec(
            TOOL_GITHUB_POST_PR_COMMENT,
            GithubPostPrCommentArgs,
            _h_post_pr_comment,
            read_only=False,
        ),
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


__all__ = [
    "find_open_pr",
    "fetch_merge_checks",
    "read_merge_pr",
    "read_dispatch_workflow",
    "read_release",
    "read_update_pr",
    "register",
    "resolve_open_pr_head_sha",
]
