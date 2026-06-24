from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.tools.task.plugin import TaskWatchArgs
from openminion.tools.task.routine.schemas import (
    GitHubPrReviewConfigV1,
    GitHubPrReviewCursorV1,
    RoutinePayloadV1,
)


def _baseline_watch_args() -> dict:
    return {
        "description": "watch desc",
        "check_instruction": "do a check",
        "interval_minutes": 5,
        "alert_condition": "always",
    }


def test_routine_payload_round_trip() -> None:
    payload = RoutinePayloadV1(
        config=GitHubPrReviewConfigV1(owner="o", repo="r"),
    )
    dumped = payload.model_dump()
    revived = RoutinePayloadV1.model_validate(dumped)
    assert revived == payload
    assert revived.routine_kind == "github_pr_review"
    assert revived.routine_version == 1
    assert revived.cursor.consecutive_failures == 0


def test_routine_payload_with_cursor_round_trips() -> None:
    cursor = GitHubPrReviewCursorV1(
        last_check_iso="2026-05-05T12:00:00Z",
        last_review_per_pr={
            "123": {"head_sha": "abc1234", "reviewed_at": "2026-05-05T11:00:00Z"}
        },
        seen_pr_numbers=[120, 121, 123],
        delivered_findings_hashes={"123": ["sha256:hash-a"]},
        consecutive_failures=2,
    )
    payload = RoutinePayloadV1(
        config=GitHubPrReviewConfigV1(owner="o", repo="r"),
        cursor=cursor,
    )
    revived = RoutinePayloadV1.model_validate(payload.model_dump())
    assert revived.cursor.consecutive_failures == 2
    assert revived.cursor.seen_pr_numbers == [120, 121, 123]
    assert revived.cursor.last_review_per_pr["123"].head_sha == "abc1234"


def test_unknown_routine_kind_fails_validation() -> None:
    with pytest.raises(ValidationError):
        RoutinePayloadV1.model_validate(
            {
                "routine_kind": "totally_made_up",
                "config": {"owner": "o", "repo": "r"},
            }
        )


def test_task_watch_args_accepts_routine_field() -> None:
    args_dict = _baseline_watch_args()
    args_dict["routine"] = {
        "config": {"owner": "octocat", "repo": "hello-world"},
    }
    parsed = TaskWatchArgs.model_validate(args_dict)
    assert parsed.routine is not None
    assert parsed.routine.routine_kind == "github_pr_review"
    assert parsed.routine.config.owner == "octocat"
    assert parsed.routine.config.repo == "hello-world"


def test_task_watch_args_routine_unknown_kind_fails() -> None:
    args_dict = _baseline_watch_args()
    args_dict["routine"] = {
        "routine_kind": "made_up_kind",
        "config": {"owner": "o", "repo": "r"},
    }
    with pytest.raises(ValidationError):
        TaskWatchArgs.model_validate(args_dict)


def test_task_watch_args_without_routine_round_trips_plain() -> None:
    parsed = TaskWatchArgs.model_validate(_baseline_watch_args())
    assert parsed.routine is None


def test_task_watch_args_extra_fields_still_dropped() -> None:
    args_dict = _baseline_watch_args()
    args_dict["bogus_unknown_field"] = "value"
    parsed = TaskWatchArgs.model_validate(args_dict)
    assert not hasattr(parsed, "bogus_unknown_field")


def test_routine_payload_dump_is_json_serializable() -> None:
    import json

    payload = RoutinePayloadV1(
        config=GitHubPrReviewConfigV1(owner="o", repo="r"),
    )
    json.dumps(payload.model_dump(mode="json"))


def test_github_pr_review_routine_rejects_interval_below_5_minutes() -> None:
    args_dict = _baseline_watch_args()
    args_dict["interval_minutes"] = 3
    args_dict["routine"] = {
        "config": {"owner": "o", "repo": "r"},
    }
    with pytest.raises(ValidationError) as exc:
        TaskWatchArgs.model_validate(args_dict)
    assert "interval_minutes >= 5" in str(exc.value)


def test_github_pr_review_routine_accepts_interval_5_minutes() -> None:
    args_dict = _baseline_watch_args()
    args_dict["interval_minutes"] = 5
    args_dict["routine"] = {"config": {"owner": "o", "repo": "r"}}
    parsed = TaskWatchArgs.model_validate(args_dict)
    assert parsed.interval_minutes == 5


def test_plain_watch_still_accepts_interval_below_5_minutes() -> None:
    args_dict = _baseline_watch_args()
    args_dict["interval_minutes"] = 1
    parsed = TaskWatchArgs.model_validate(args_dict)
    assert parsed.interval_minutes == 1
    assert parsed.routine is None
