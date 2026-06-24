from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.tools.task.pr_review.schemas import (
    FindingV1,
    OpenPrFactsV1,
    PrFactsPayloadV1,
    ReviewOutcomePayloadV1,
    ReviewedPrV1,
    build_pr_facts_payload,
    finding_hash,
    validate_review_outcome,
)
from openminion.tools.task.routine.schemas import GitHubPrReviewCursorV1


# PR facts payload + builder


def test_pr_facts_payload_round_trip() -> None:
    payload = PrFactsPayloadV1(
        routine_id="job-1",
        repo="o/r",
        checked_at="2026-05-05T12:00:00Z",
        open_prs=[OpenPrFactsV1(number=1, head_sha="abc")],
    )
    revived = PrFactsPayloadV1.model_validate(payload.model_dump())
    assert revived == payload


def test_builder_excludes_pr_with_unchanged_head_sha() -> None:
    cursor = GitHubPrReviewCursorV1(
        last_review_per_pr={
            "1": {"head_sha": "abc", "reviewed_at": "2026-05-05T11:00:00Z"}
        },
        seen_pr_numbers=[1],
    )
    raw = [{"number": 1, "head_sha": "abc", "title": "PR 1"}]
    payload = build_pr_facts_payload(
        routine_id="job-1", repo="o/r", open_prs_raw=raw, cursor=cursor
    )
    # Head-SHA dedupe excluded the PR from actionable list.
    assert payload.open_prs == []
    assert payload.previously_seen_prs == [1]


def test_builder_includes_pr_with_changed_head_sha() -> None:
    cursor = GitHubPrReviewCursorV1(
        last_review_per_pr={
            "1": {"head_sha": "old-sha", "reviewed_at": "2026-05-05T11:00:00Z"}
        },
        seen_pr_numbers=[1],
    )
    raw = [
        {
            "number": 1,
            "head_sha": "new-sha",
            "title": "PR 1",
            "lines_added": 50,
        }
    ]
    payload = build_pr_facts_payload(
        routine_id="job-1", repo="o/r", open_prs_raw=raw, cursor=cursor
    )
    assert len(payload.open_prs) == 1
    assert payload.open_prs[0].head_sha == "new-sha"
    assert payload.open_prs[0].last_review_sha == "old-sha"
    assert payload.open_prs[0].commits_since_last_review >= 1


def test_builder_marks_newly_opened_prs() -> None:
    cursor = GitHubPrReviewCursorV1(seen_pr_numbers=[1, 2])
    raw = [
        {"number": 1, "head_sha": "a"},
        {"number": 3, "head_sha": "c"},  # new
    ]
    payload = build_pr_facts_payload(
        routine_id="job-1", repo="o/r", open_prs_raw=raw, cursor=cursor
    )
    assert payload.newly_opened_prs == [3]


def test_builder_marks_closed_since_last_check() -> None:
    cursor = GitHubPrReviewCursorV1(seen_pr_numbers=[1, 2, 3])
    raw = [{"number": 1, "head_sha": "a"}]  # 2 and 3 closed
    payload = build_pr_facts_payload(
        routine_id="job-1", repo="o/r", open_prs_raw=raw, cursor=cursor
    )
    assert sorted(payload.closed_since_last_check) == [2, 3]


# review outcome validation


def test_outcome_summary_length_capped_by_pydantic() -> None:
    long_summary = "x" * 2000
    entry = ReviewedPrV1(
        number=1,
        head_sha_reviewed="abc",
        summary=long_summary,
    )
    assert len(entry.summary) == 1000


def test_outcome_severity_enum_enforced() -> None:
    with pytest.raises(ValidationError):
        FindingV1(file="x.py", line=1, severity="urgent", message="bad")


def test_outcome_validation_drops_head_sha_mismatch() -> None:
    facts = PrFactsPayloadV1(
        routine_id="job-1",
        repo="o/r",
        checked_at="2026-05-05T12:00:00Z",
        open_prs=[OpenPrFactsV1(number=1, head_sha="abc")],
    )
    outcome = ReviewOutcomePayloadV1(
        reviewed_prs=[ReviewedPrV1(number=1, head_sha_reviewed="WRONG", summary="x")]
    )
    kept, dropped = validate_review_outcome(outcome, facts=facts)
    assert kept == []
    assert len(dropped) == 1
    assert dropped[0]["reason_code"] == "head_sha_mismatch"


def test_outcome_validation_drops_pr_not_in_facts() -> None:
    facts = PrFactsPayloadV1(
        routine_id="job-1",
        repo="o/r",
        checked_at="2026-05-05T12:00:00Z",
        open_prs=[OpenPrFactsV1(number=1, head_sha="abc")],
    )
    outcome = ReviewOutcomePayloadV1(
        reviewed_prs=[
            ReviewedPrV1(number=999, head_sha_reviewed="abc", summary="rogue")
        ]
    )
    kept, dropped = validate_review_outcome(outcome, facts=facts)
    assert kept == []
    assert dropped[0]["reason_code"] == "pr_not_in_facts"


def test_outcome_validation_keeps_matching_entries() -> None:
    facts = PrFactsPayloadV1(
        routine_id="job-1",
        repo="o/r",
        checked_at="2026-05-05T12:00:00Z",
        open_prs=[OpenPrFactsV1(number=1, head_sha="abc")],
    )
    outcome = ReviewOutcomePayloadV1(
        reviewed_prs=[ReviewedPrV1(number=1, head_sha_reviewed="abc", summary="ok")]
    )
    kept, dropped = validate_review_outcome(outcome, facts=facts)
    assert len(kept) == 1
    assert dropped == []


def test_finding_hash_is_stable() -> None:
    f = FindingV1(file="x.py", line=42, severity="warn", message="stale import")
    h1 = finding_hash(pr_number=1, head_sha="abc", finding=f)
    h2 = finding_hash(pr_number=1, head_sha="abc", finding=f)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_finding_hash_changes_when_head_sha_changes() -> None:
    f = FindingV1(file="x.py", line=42, severity="warn", message="stale import")
    h_old = finding_hash(pr_number=1, head_sha="abc", finding=f)
    h_new = finding_hash(pr_number=1, head_sha="def", finding=f)
    assert h_old != h_new
