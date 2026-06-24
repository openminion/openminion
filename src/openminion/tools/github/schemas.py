"""GitHub tool schemas."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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

    @field_validator("head_sha", mode="before")
    @classmethod
    def _normalize_sha(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("head_sha is required")
        if not all(ch in "0123456789abcdefABCDEF" for ch in token):
            raise ValueError("head_sha must be a hex string")
        return token.lower()


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
    "GithubPostPrReviewArgs",
    "GithubPostPrCommentArgs",
]
