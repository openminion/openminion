from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROUTINE_KIND_GITHUB_PR_REVIEW = "github_pr_review"

ROUTINE_VERSION_V1 = 1


class GitHubPrReviewConfigV1(BaseModel):
    """Operator-supplied configuration for a github_pr_review routine."""

    model_config = ConfigDict(extra="forbid")

    owner: str = Field(..., min_length=1, description="GitHub owner / org")
    repo: str = Field(..., min_length=1, description="GitHub repo slug")
    state_filter: Literal["open", "closed", "all"] = Field(
        default="open",
        description="PR state filter passed to github.list_prs.",
    )


class _PerPrCursorEntryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head_sha: str = Field(..., description="Last reviewed head SHA")
    reviewed_at: str = Field(..., description="ISO8601 review timestamp")


class GitHubPrReviewCursorV1(BaseModel):
    """Durable cursor for a github_pr_review routine."""

    model_config = ConfigDict(extra="forbid")

    last_check_iso: str | None = Field(default=None)
    last_review_per_pr: dict[str, _PerPrCursorEntryV1] = Field(default_factory=dict)
    seen_pr_numbers: list[int] = Field(default_factory=list)
    delivered_findings_hashes: dict[str, list[str]] = Field(default_factory=dict)
    consecutive_failures: int = Field(default=0, ge=0)


class RoutinePayloadV1(BaseModel):
    """Typed routine block carried inside ``task.watch`` payloads.

    V1 ships exactly one routine kind. Unknown values fail validation rather
    than silently fall through.
    """

    model_config = ConfigDict(extra="forbid")

    routine_kind: Literal["github_pr_review"] = Field(
        default=ROUTINE_KIND_GITHUB_PR_REVIEW,
        description="Discriminator for the routine kind.",
    )
    routine_version: int = Field(default=ROUTINE_VERSION_V1, ge=1)
    config: GitHubPrReviewConfigV1
    cursor: GitHubPrReviewCursorV1 = Field(default_factory=GitHubPrReviewCursorV1)


__all__ = [
    "ROUTINE_KIND_GITHUB_PR_REVIEW",
    "ROUTINE_VERSION_V1",
    "GitHubPrReviewConfigV1",
    "GitHubPrReviewCursorV1",
    "RoutinePayloadV1",
]
