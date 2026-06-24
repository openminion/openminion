import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..constants import PR_REVIEW_SUMMARY_MAX_CHARS
from ..routine.schemas import GitHubPrReviewCursorV1


class OpenPrFactsV1(BaseModel):
    """Typed facts about a single open pull request."""

    model_config = ConfigDict(extra="forbid")

    number: int = Field(..., ge=1)
    title: str = Field(default="")
    author: str = Field(default="")
    head_sha: str = Field(..., min_length=1)
    base_ref: str = Field(default="main")
    head_ref: str = Field(default="")
    draft: bool = Field(default=False)
    mergeable_state: str = Field(default="unknown")
    checks_status: Literal["passing", "failing", "pending", "none"] = Field(
        default="none"
    )
    labels: list[str] = Field(default_factory=list)
    review_state: Literal["approved", "changes_requested", "none"] = Field(
        default="none"
    )
    last_review_sha: str | None = Field(default=None)
    commits_since_last_review: int = Field(default=0, ge=0)
    lines_added: int = Field(default=0, ge=0)
    lines_deleted: int = Field(default=0, ge=0)
    diff_truncated: bool = Field(default=False)
    diff_preview: str = Field(default="")
    comments_count: int = Field(default=0, ge=0)
    url: str = Field(default="")


class PrFactsPayloadV1(BaseModel):
    """Top-level PR facts payload handed to the model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    routine_id: str = Field(..., min_length=1)
    repo: str = Field(..., min_length=1)
    checked_at: str = Field(..., min_length=1)
    since_last_check: str | None = Field(default=None)
    open_prs: list[OpenPrFactsV1] = Field(default_factory=list)
    newly_opened_prs: list[int] = Field(default_factory=list)
    closed_since_last_check: list[int] = Field(default_factory=list)
    previously_seen_prs: list[int] = Field(default_factory=list)


class FindingV1(BaseModel):
    """One review finding emitted by the model."""

    model_config = ConfigDict(extra="forbid")

    file: str = Field(default="")
    line: int = Field(default=0, ge=0)
    severity: Literal["info", "warn", "error"] = Field(default="info")
    message: str = Field(..., min_length=1)


class ReviewedPrV1(BaseModel):
    """One PR review entry emitted by the model."""

    model_config = ConfigDict(extra="forbid")

    number: int = Field(..., ge=1)
    head_sha_reviewed: str = Field(..., min_length=1)
    review_state: Literal["needs_human_review", "approved_lgtm", "needs_changes"] = (
        Field(default="needs_human_review")
    )
    summary: str = Field(default="")
    findings: list[FindingV1] = Field(default_factory=list)

    @field_validator("summary", mode="after")
    @classmethod
    def _cap_summary_length(cls, value: str) -> str:
        # Spec § 6.2 rule 4: runtime-side truncation, not model-side.
        if len(value) > PR_REVIEW_SUMMARY_MAX_CHARS:
            return value[:PR_REVIEW_SUMMARY_MAX_CHARS]
        return value


class SkippedPrV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(..., ge=1)
    reason: str = Field(default="")


class ReviewOutcomePayloadV1(BaseModel):
    """Top-level review outcome the model returns inside the trailer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    reviewed_prs: list[ReviewedPrV1] = Field(default_factory=list)
    skipped_prs: list[SkippedPrV1] = Field(default_factory=list)


# Builder + validator helpers


def build_pr_facts_payload(
    *,
    routine_id: str,
    repo: str,
    open_prs_raw: list[dict[str, Any]],
    cursor: GitHubPrReviewCursorV1,
    checked_at: str | None = None,
) -> PrFactsPayloadV1:
    """Build the typed PR facts payload from raw github tool results + cursor."""
    if checked_at is None:
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    actionable: list[OpenPrFactsV1] = []
    open_pr_numbers_now: list[int] = []

    for raw in open_prs_raw:
        if not isinstance(raw, dict):
            continue
        number = raw.get("number")
        head_sha = raw.get("head_sha") or ""
        if not isinstance(number, int) or not head_sha:
            continue
        open_pr_numbers_now.append(number)

        cursor_entry = cursor.last_review_per_pr.get(str(number))
        last_sha = cursor_entry.head_sha if cursor_entry is not None else None

        # Head-SHA dedupe: skip PRs whose SHA matches the last reviewed SHA.
        if last_sha == head_sha:
            continue

        commits_since = int(raw.get("commits_since_last_review", 0) or 0)
        if commits_since == 0 and last_sha is not None:
            # Best-effort default when raw payload omits the count.
            commits_since = 1

        actionable.append(
            OpenPrFactsV1(
                number=number,
                title=str(raw.get("title", "")),
                author=str(raw.get("author", "")),
                head_sha=head_sha,
                base_ref=str(raw.get("base_ref", "main")),
                head_ref=str(raw.get("head_ref", "")),
                draft=bool(raw.get("draft", False)),
                mergeable_state=str(raw.get("mergeable_state", "unknown")),
                checks_status=raw.get("checks_status", "none"),
                labels=list(raw.get("labels", [])),
                review_state=raw.get("review_state", "none"),
                last_review_sha=last_sha,
                commits_since_last_review=commits_since,
                lines_added=int(raw.get("lines_added", 0) or 0),
                lines_deleted=int(raw.get("lines_deleted", 0) or 0),
                diff_truncated=bool(raw.get("diff_truncated", False)),
                diff_preview=str(raw.get("diff_preview", "")),
                comments_count=int(raw.get("comments_count", 0) or 0),
                url=str(raw.get("url", "")),
            )
        )

    seen_set = set(cursor.seen_pr_numbers)
    newly_opened = [n for n in open_pr_numbers_now if n not in seen_set]
    closed_since_last_check = [
        n for n in cursor.seen_pr_numbers if n not in open_pr_numbers_now
    ]

    return PrFactsPayloadV1(
        routine_id=routine_id,
        repo=repo,
        checked_at=checked_at,
        since_last_check=cursor.last_check_iso,
        open_prs=actionable,
        newly_opened_prs=newly_opened,
        closed_since_last_check=closed_since_last_check,
        previously_seen_prs=sorted(seen_set),
    )


# Outcome validation (spec § 6.2 rules)


class OutcomeValidationError(Exception):
    """Internal carrier for outcome validation problems with stable codes."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def validate_review_outcome(
    outcome: ReviewOutcomePayloadV1,
    *,
    facts: PrFactsPayloadV1,
) -> tuple[list[ReviewedPrV1], list[dict[str, Any]]]:
    """Apply spec § 6.2 validation rules to a parsed outcome."""
    open_by_number = {pr.number: pr for pr in facts.open_prs}
    kept: list[ReviewedPrV1] = []
    dropped: list[dict[str, Any]] = []

    for entry in outcome.reviewed_prs:
        ref = open_by_number.get(entry.number)
        if ref is None:
            dropped.append(
                {
                    "number": entry.number,
                    "reason_code": "pr_not_in_facts",
                    "detail": (
                        f"PR #{entry.number} was not in the actionable open_prs list."
                    ),
                }
            )
            continue
        if ref.head_sha != entry.head_sha_reviewed:
            dropped.append(
                {
                    "number": entry.number,
                    "reason_code": "head_sha_mismatch",
                    "detail": (
                        f"head_sha_reviewed={entry.head_sha_reviewed!r} does "
                        f"not match runtime head_sha={ref.head_sha!r}."
                    ),
                }
            )
            continue
        kept.append(entry)
    return kept, dropped


def finding_hash(*, pr_number: int, head_sha: str, finding: FindingV1) -> str:
    """Stable hash for finding-level dedupe.

    Per spec D7 (2): ``sha256(pr_number || head_sha || file || line || message)``.
    """
    payload = "||".join(
        [
            str(pr_number),
            head_sha,
            finding.file or "",
            str(finding.line),
            finding.message,
        ]
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "OpenPrFactsV1",
    "PrFactsPayloadV1",
    "FindingV1",
    "ReviewedPrV1",
    "SkippedPrV1",
    "ReviewOutcomePayloadV1",
    "build_pr_facts_payload",
    "OutcomeValidationError",
    "validate_review_outcome",
    "finding_hash",
]
