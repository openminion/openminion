from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import DEFAULT_GITHUB_DIFF_MAX_LINES


def _normalize_owner_repo(value: Any, *, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    token = str(value).strip()
    if not token:
        raise ValueError(f"{field} is required")
    if "/" in token or ".." in token:
        raise ValueError(f"{field} must be a single path segment")
    return token


class _RepoArgsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str = Field(..., min_length=1, description="GitHub owner / org")
    repo: str = Field(..., min_length=1, description="GitHub repo slug")

    @field_validator("owner", mode="before")
    @classmethod
    def _normalize_owner(cls, value: Any) -> str:
        return _normalize_owner_repo(value, field="owner")

    @field_validator("repo", mode="before")
    @classmethod
    def _normalize_repo(cls, value: Any) -> str:
        return _normalize_owner_repo(value, field="repo")


class GithubListPrsArgs(_RepoArgsBase):
    state: str = Field(
        default="open",
        description="PR state filter: open|closed|all",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum PRs to return.",
    )

    @field_validator("state", mode="before")
    @classmethod
    def _normalize_state(cls, value: Any) -> str:
        token = str(value or "open").strip().lower()
        if token not in {"open", "closed", "all"}:
            raise ValueError("state must be one of open|closed|all")
        return token


class _PrArgsBase(_RepoArgsBase):
    number: int = Field(..., ge=1, description="Pull request number")


class GithubFetchPrArgs(_PrArgsBase):
    pass


class GithubFetchDiffArgs(_PrArgsBase):
    max_lines: int = Field(
        default=DEFAULT_GITHUB_DIFF_MAX_LINES,
        ge=10,
        le=10_000,
        description=(
            "Truncate the diff to this many lines. The response includes a "
            "`truncated` flag when truncation occurs."
        ),
    )


class GithubFetchCommentsArgs(_PrArgsBase):
    limit: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum comments to return.",
    )


class GithubFetchChecksArgs(_RepoArgsBase):
    head_sha: str = Field(..., min_length=7, description="Commit SHA")
    expected_checks: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Exact check-run names required for an overall success result.",
    )

    @field_validator("head_sha", mode="before")
    @classmethod
    def _normalize_sha(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("head_sha is required")
        if not all(ch in "0123456789abcdefABCDEF" for ch in token):
            raise ValueError("head_sha must be a hex string")
        return token.lower()

    @field_validator("expected_checks")
    @classmethod
    def _validate_expected_checks(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("expected_checks cannot contain empty names")
        if len(set(normalized)) != len(normalized):
            raise ValueError("expected_checks cannot contain duplicates")
        return normalized


def _normalize_branch(value: Any, *, field: str) -> str:
    if value is None:
        raise ValueError(f"{field} is required")
    token = str(value).strip()
    if not token:
        raise ValueError(f"{field} is required")
    if token.startswith("/") or ".." in token:
        raise ValueError(f"{field} contains an invalid branch token")
    return token


def _normalize_message(value: Any, *, field: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{field} is required")
    return token


def _normalize_path(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError("path is required")
    if token.startswith("/") or ".." in token.split("/"):
        raise ValueError("path must stay within the repository")
    return token


class GithubCommitFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, description="Repository-relative file path")
    content: str = Field(..., description="UTF-8 file content")

    @field_validator("path", mode="before")
    @classmethod
    def _validate_path(cls, value: Any) -> str:
        return _normalize_path(value)

    @field_validator("content", mode="before")
    @classmethod
    def _validate_content(cls, value: Any) -> str:
        return str(value or "")


class GithubCommitFilesArgs(_RepoArgsBase):
    branch: str = Field(..., min_length=1, description="Target smoke branch name")
    base_branch: str = Field(
        ...,
        min_length=1,
        description="Base branch/ref used when creating the smoke branch",
    )
    message: str = Field(..., min_length=1, description="Commit message")
    files: list[GithubCommitFileInput] = Field(
        ...,
        min_length=1,
        description="Files to write in a single commit",
    )
    force: bool = Field(
        default=False,
        description="Force-update semantics are denied in L3.",
    )

    @field_validator("branch", mode="before")
    @classmethod
    def _validate_branch(cls, value: Any) -> str:
        return _normalize_branch(value, field="branch")

    @field_validator("base_branch", mode="before")
    @classmethod
    def _validate_base_branch(cls, value: Any) -> str:
        return _normalize_branch(value, field="base_branch")

    @field_validator("message", mode="before")
    @classmethod
    def _validate_message(cls, value: Any) -> str:
        return _normalize_message(value, field="message")


class GithubOpenPrArgs(_RepoArgsBase):
    head: str = Field(..., min_length=1, description="Head smoke branch")
    base: str = Field(..., min_length=1, description="Base branch")
    title: str = Field(..., min_length=1, description="PR title")
    body: str = Field(..., min_length=1, description="PR body")

    @field_validator("head", mode="before")
    @classmethod
    def _validate_head(cls, value: Any) -> str:
        return _normalize_branch(value, field="head")

    @field_validator("base", mode="before")
    @classmethod
    def _validate_base(cls, value: Any) -> str:
        return _normalize_branch(value, field="base")

    @field_validator("title", "body", mode="before")
    @classmethod
    def _validate_text(cls, value: Any, info: Any) -> str:
        return _normalize_message(value, field=str(info.field_name or "value"))


class GithubUpdatePrArgs(_PrArgsBase):
    title: str | None = Field(default=None, description="Replacement PR title")
    body: str | None = Field(default=None, description="Replacement PR body")

    @field_validator("title", "body", mode="before")
    @classmethod
    def _validate_text(cls, value: Any, info: Any) -> str | None:
        if value is None:
            return None
        return _normalize_message(value, field=str(info.field_name or "value"))

    @model_validator(mode="after")
    def _require_update(self) -> "GithubUpdatePrArgs":
        if self.title is None and self.body is None:
            raise ValueError("title or body is required")
        return self


class GithubMergePrArgs(_PrArgsBase):
    expected_head_sha: str = Field(
        ..., min_length=7, description="Approved pull-request head commit SHA"
    )
    merge_method: str = Field(..., description="Merge method: merge|squash|rebase")
    expected_checks: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Exact check-run names required before merge.",
    )

    @field_validator("expected_head_sha", mode="before")
    @classmethod
    def _normalize_expected_head_sha(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token or not all(ch in "0123456789abcdefABCDEF" for ch in token):
            raise ValueError("expected_head_sha must be a hex string")
        return token.lower()

    @field_validator("merge_method", mode="before")
    @classmethod
    def _normalize_merge_method(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        if token not in {"merge", "squash", "rebase"}:
            raise ValueError("merge_method must be one of merge|squash|rebase")
        return token

    @field_validator("expected_checks")
    @classmethod
    def _validate_expected_checks(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("expected_checks cannot contain empty names")
        if len(set(normalized)) != len(normalized):
            raise ValueError("expected_checks cannot contain duplicates")
        return normalized


def _normalize_workflow(value: Any) -> str:
    token = _normalize_message(value, field="workflow")
    if "/" in token or ".." in token:
        raise ValueError("workflow must be a workflow file name or numeric ID")
    return token


class GithubDispatchWorkflowArgs(_RepoArgsBase):
    workflow: str = Field(..., min_length=1, description="Allowlisted workflow file")
    ref: str = Field(..., min_length=1, description="Allowlisted branch or tag ref")
    request_id: str = Field(..., min_length=1, max_length=128)
    target: str = Field(..., min_length=1, max_length=64)
    inputs: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("workflow", mode="before")
    @classmethod
    def _validate_workflow(cls, value: Any) -> str:
        return _normalize_workflow(value)

    @field_validator("ref", mode="before")
    @classmethod
    def _validate_ref(cls, value: Any) -> str:
        return _normalize_branch(value, field="ref")

    @field_validator("request_id", "target", mode="before")
    @classmethod
    def _validate_identifier(cls, value: Any, info: Any) -> str:
        return _normalize_message(value, field=str(info.field_name or "value"))

    @field_validator("inputs", mode="before")
    @classmethod
    def _validate_inputs(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise ValueError("inputs must be an object")
        normalized = {
            str(key).strip(): str(item).strip() for key, item in value.items()
        }
        if any(not key or not item for key, item in normalized.items()):
            raise ValueError("workflow input keys and values cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_target_input(self) -> "GithubDispatchWorkflowArgs":
        if self.inputs.get("target") != self.target:
            raise ValueError("inputs.target must match target")
        return self


class GithubListWorkflowRunsArgs(_RepoArgsBase):
    workflow: str = Field(..., min_length=1, description="Workflow file or numeric ID")
    ref: str = Field(..., min_length=1, description="Branch or tag ref")
    request_id: str = Field(..., min_length=1, max_length=128)
    event: str = Field(default="workflow_dispatch")
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("workflow", mode="before")
    @classmethod
    def _validate_workflow(cls, value: Any) -> str:
        return _normalize_workflow(value)

    @field_validator("ref", mode="before")
    @classmethod
    def _validate_ref(cls, value: Any) -> str:
        return _normalize_branch(value, field="ref")

    @field_validator("request_id", mode="before")
    @classmethod
    def _validate_request_id(cls, value: Any) -> str:
        return _normalize_message(value, field="request_id")

    @field_validator("event", mode="before")
    @classmethod
    def _validate_event(cls, value: Any) -> str:
        token = str(value or "workflow_dispatch").strip()
        if token != "workflow_dispatch":
            raise ValueError("event must be workflow_dispatch")
        return token


class GithubCreateReleaseArgs(_RepoArgsBase):
    tag: str = Field(..., min_length=1, description="Existing Git tag")
    expected_commit_sha: str = Field(
        ..., min_length=7, description="Expected dereferenced tag commit SHA"
    )
    title: str = Field(..., min_length=1)
    notes: str = Field(..., min_length=1)
    draft: bool
    prerelease: bool

    @field_validator("tag", mode="before")
    @classmethod
    def _validate_tag(cls, value: Any) -> str:
        return _normalize_branch(value, field="tag")

    @field_validator("expected_commit_sha", mode="before")
    @classmethod
    def _validate_commit_sha(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token or not all(ch in "0123456789abcdefABCDEF" for ch in token):
            raise ValueError("expected_commit_sha must be a hex string")
        return token.lower()

    @field_validator("title", "notes", mode="before")
    @classmethod
    def _validate_release_text(cls, value: Any, info: Any) -> str:
        return _normalize_message(value, field=str(info.field_name or "value"))


class GithubPostPrReviewArgs(_PrArgsBase):
    event: str = Field(..., min_length=1, description="L3 allows COMMENT only.")
    body: str = Field(..., min_length=1, description="Review body")

    @field_validator("event", mode="before")
    @classmethod
    def _normalize_event(cls, value: Any) -> str:
        token = str(value or "").strip().upper()
        if token != "COMMENT":
            raise ValueError("event must be COMMENT in L3")
        return token

    @field_validator("body", mode="before")
    @classmethod
    def _validate_body(cls, value: Any) -> str:
        return _normalize_message(value, field="body")


class GithubPostPrCommentArgs(_PrArgsBase):
    body: str = Field(..., min_length=1, description="Issue comment body")

    @field_validator("body", mode="before")
    @classmethod
    def _validate_body(cls, value: Any) -> str:
        return _normalize_message(value, field="body")


__all__ = [
    "GithubListPrsArgs",
    "GithubFetchPrArgs",
    "GithubFetchDiffArgs",
    "GithubFetchCommentsArgs",
    "GithubFetchChecksArgs",
    "GithubCommitFileInput",
    "GithubCommitFilesArgs",
    "GithubOpenPrArgs",
    "GithubUpdatePrArgs",
    "GithubMergePrArgs",
    "GithubDispatchWorkflowArgs",
    "GithubListWorkflowRunsArgs",
    "GithubCreateReleaseArgs",
    "GithubPostPrReviewArgs",
    "GithubPostPrCommentArgs",
]
