import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .social import (
    ROUTINE_KIND_SOCIAL_SIGNAL,
    SocialSignalConfigV1,
    SocialSignalCursorV1,
)

ROUTINE_KIND_GITHUB_PR_REVIEW: Literal["github_pr_review"] = "github_pr_review"

ROUTINE_VERSION_V1 = 1


class GitHubPrReviewConfigV1(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    last_check_iso: str | None = Field(default=None)
    last_review_per_pr: dict[str, _PerPrCursorEntryV1] = Field(default_factory=dict)
    seen_pr_numbers: list[int] = Field(default_factory=list)
    delivered_findings_hashes: dict[str, list[str]] = Field(default_factory=dict)
    consecutive_failures: int = Field(default=0, ge=0)


class RoutinePayloadV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routine_kind: Literal["github_pr_review", "social_signal"] = Field(
        default=ROUTINE_KIND_GITHUB_PR_REVIEW,
        description="Discriminator for the routine kind.",
    )
    routine_version: int = Field(default=ROUTINE_VERSION_V1, ge=1)
    config: GitHubPrReviewConfigV1 | SocialSignalConfigV1
    cursor: GitHubPrReviewCursorV1 | SocialSignalCursorV1 = Field(
        default_factory=GitHubPrReviewCursorV1
    )

    @model_validator(mode="before")
    @classmethod
    def _select_domain_models(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if payload.get("routine_kind") != ROUTINE_KIND_SOCIAL_SIGNAL:
            return payload
        payload["config"] = SocialSignalConfigV1.model_validate(payload.get("config"))
        payload["cursor"] = SocialSignalCursorV1.model_validate(
            payload.get("cursor") or {}
        )
        return payload

    @model_validator(mode="after")
    def _validate_domain_pair(self) -> "RoutinePayloadV1":
        if self.routine_kind == ROUTINE_KIND_GITHUB_PR_REVIEW:
            if not isinstance(self.config, GitHubPrReviewConfigV1) or not isinstance(
                self.cursor, GitHubPrReviewCursorV1
            ):
                raise ValueError("github_pr_review config and cursor do not match")
            return self
        if not isinstance(self.config, SocialSignalConfigV1) or not isinstance(
            self.cursor, SocialSignalCursorV1
        ):
            raise ValueError("social_signal config and cursor do not match")
        return self


__all__ = [
    "ROUTINE_KIND_GITHUB_PR_REVIEW",
    "ROUTINE_KIND_SOCIAL_SIGNAL",
    "ROUTINE_VERSION_V1",
    "GitHubPrReviewConfigV1",
    "GitHubPrReviewCursorV1",
    "RoutinePayloadV1",
]
