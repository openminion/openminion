from __future__ import annotations

from openminion.tools.task.pr_review.renderer import (
    render_announce_summary,
    render_artifact_markdown,
)
from openminion.tools.task.pr_review.schemas import (
    FindingV1,
    ReviewOutcomePayloadV1,
    ReviewedPrV1,
    SkippedPrV1,
)


def test_render_artifact_empty_outcome_is_stable() -> None:
    out = render_artifact_markdown(
        routine_id="job-1",
        repo="octocat/hello-world",
        checked_at="2026-05-05T12:00:00Z",
        outcome=ReviewOutcomePayloadV1(),
    )
    expected = (
        "# GitHub PR Review — octocat/hello-world\n"
        "\n"
        "Routine: job-1\n"
        "Checked at: 2026-05-05T12:00:00Z\n"
        "Reviewed: 0\n"
        "Skipped: 0\n"
    )
    assert out == expected


def test_render_artifact_with_findings_is_stable() -> None:
    outcome = ReviewOutcomePayloadV1(
        reviewed_prs=[
            ReviewedPrV1(
                number=42,
                head_sha_reviewed="abc1234",
                review_state="needs_changes",
                summary="Add tests for the new helper.",
                findings=[
                    FindingV1(
                        file="src/x.py",
                        line=10,
                        severity="warn",
                        message="missing docstring",
                    ),
                    FindingV1(
                        file="",
                        line=0,
                        severity="info",
                        message="overall: looks reasonable",
                    ),
                ],
            )
        ],
        skipped_prs=[SkippedPrV1(number=43, reason="no_change_since_last_review")],
    )
    out = render_artifact_markdown(
        routine_id="job-2",
        repo="o/r",
        checked_at="2026-05-05T12:34:56Z",
        outcome=outcome,
    )
    assert "# GitHub PR Review — o/r" in out
    assert "## #42 [changes]" in out
    assert "Head SHA: abc1234" in out
    assert "Summary: Add tests for the new helper." in out
    assert "- [warn] src/x.py:10 — missing docstring" in out
    assert "- [info] overall: looks reasonable" in out
    assert "## Skipped" in out
    assert "- #43: no_change_since_last_review" in out


def test_render_announce_summary_basic() -> None:
    outcome = ReviewOutcomePayloadV1(
        reviewed_prs=[
            ReviewedPrV1(number=1, head_sha_reviewed="a", findings=[]),
            ReviewedPrV1(
                number=2,
                head_sha_reviewed="b",
                findings=[FindingV1(message="x")],
            ),
        ]
    )
    line = render_announce_summary(repo="o/r", outcome=outcome)
    assert line == "PR review run for o/r: reviewed 2 PR(s), 1 finding(s)."


def test_render_announce_summary_caps_length() -> None:
    repo = "o/" + ("very-long-repo-name-" * 30)
    outcome = ReviewOutcomePayloadV1()
    line = render_announce_summary(repo=repo, outcome=outcome)
    assert len(line) <= 200
    assert line.endswith("…")


def test_renderer_is_deterministic_across_calls() -> None:
    outcome = ReviewOutcomePayloadV1(
        reviewed_prs=[
            ReviewedPrV1(
                number=1,
                head_sha_reviewed="abc",
                summary="ok",
                findings=[FindingV1(message="m")],
            )
        ]
    )
    a = render_artifact_markdown(
        routine_id="j", repo="o/r", checked_at="t", outcome=outcome
    )
    b = render_artifact_markdown(
        routine_id="j", repo="o/r", checked_at="t", outcome=outcome
    )
    assert a == b
