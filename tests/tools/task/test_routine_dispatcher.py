from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from openminion.tools.github.interfaces import TOOL_GITHUB_LIST_PRS
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY
from openminion.tools.task.routine.dispatcher import (
    GitHubPrReviewHandler,
    build_default_dispatcher,
    parse_routine_outcome_trailer,
)
from openminion.tools.task.routine.schemas import (
    GitHubPrReviewConfigV1,
    GitHubPrReviewCursorV1,
    RoutinePayloadV1,
)


class _StubPreTurnContext:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self._response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def invoke_tool(self, *, name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((name, dict(args)))
        return self._response


class _StubSink:
    def __init__(self) -> None:
        self.artifacts: list[tuple[str, str]] = []
        self.announces: list[tuple[str, str]] = []

    def write_artifact(self, *, routine_id: str, body: str) -> str:
        artifact_id = f"artifact://{routine_id}/{len(self.artifacts)}"
        self.artifacts.append((artifact_id, body))
        return artifact_id

    def announce(self, *, routine_id: str, summary: str) -> None:
        self.announces.append((routine_id, summary))


def _routine() -> RoutinePayloadV1:
    return RoutinePayloadV1(
        config=GitHubPrReviewConfigV1(owner="octocat", repo="hello-world"),
        cursor=GitHubPrReviewCursorV1(),
    )


def test_trailer_missing_returns_trailer_missing_code() -> None:
    result = parse_routine_outcome_trailer("just prose, no trailer")
    assert result.outcome is None
    assert result.reason_code == "trailer_missing"


def test_trailer_malformed_json_returns_distinct_code() -> None:
    result = parse_routine_outcome_trailer(
        "noise <routine_outcome>{not json</routine_outcome> tail"
    )
    assert result.outcome is None
    assert result.reason_code == "trailer_malformed_json"


def test_outcome_validation_failed_returns_distinct_code() -> None:
    payload = json.dumps({"reviewed_prs": [{"number": "not-an-int"}]})
    result = parse_routine_outcome_trailer(
        f"<routine_outcome>{payload}</routine_outcome>"
    )
    assert result.outcome is None
    assert result.reason_code == "outcome_validation_failed"


def test_trailer_parses_valid_outcome() -> None:
    payload = json.dumps(
        {
            "reviewed_prs": [
                {
                    "number": 42,
                    "head_sha_reviewed": "abc",
                    "summary": "ok",
                }
            ]
        }
    )
    result = parse_routine_outcome_trailer(
        f"prologue <routine_outcome>{payload}</routine_outcome> trailing"
    )
    assert result.outcome is not None
    assert result.reason_code is None
    assert result.outcome.reviewed_prs[0].number == 42


def test_dispatcher_recognizes_routine_payload() -> None:
    dispatcher = build_default_dispatcher()
    payload = {
        "kind": "agentTurn",
        WATCH_PAYLOAD_KEY: {
            "routine": _routine().model_dump(mode="json"),
        },
    }
    assert dispatcher.is_routine_payload(payload) is True
    routine = dispatcher.routine_for(payload)
    assert routine is not None
    assert routine.config.owner == "octocat"


def test_dispatcher_ignores_plain_watch_payload() -> None:
    dispatcher = build_default_dispatcher()
    payload = {
        "kind": "agentTurn",
        WATCH_PAYLOAD_KEY: {"description": "plain watch"},
    }
    assert dispatcher.is_routine_payload(payload) is False
    assert dispatcher.routine_for(payload) is None


def test_dispatcher_ignores_payload_without_watch_block() -> None:
    dispatcher = build_default_dispatcher()
    assert dispatcher.is_routine_payload({"kind": "agentTurn"}) is False


def test_dispatcher_get_returns_handler_for_kind() -> None:
    dispatcher = build_default_dispatcher()
    handler = dispatcher.get("github_pr_review")
    assert handler is not None
    assert handler.routine_kind == "github_pr_review"


def test_dispatcher_get_returns_none_for_unknown_kind() -> None:
    dispatcher = build_default_dispatcher()
    assert dispatcher.get("not_registered") is None


def test_pre_turn_invokes_github_list_prs_through_tool_runtime() -> None:
    handler = GitHubPrReviewHandler()
    routine = _routine()
    ctx = _StubPreTurnContext(
        response={
            "ok": True,
            "data": {
                "open_prs": [
                    {"number": 1, "head_sha": "abc", "title": "PR 1"},
                    {"number": 2, "head_sha": "def", "title": "PR 2"},
                ]
            },
        }
    )
    facts = handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)
    assert ctx.calls == [
        (
            TOOL_GITHUB_LIST_PRS,
            {"owner": "octocat", "repo": "hello-world", "state": "open"},
        )
    ]
    assert len(facts.open_prs) == 2
    assert facts.repo == "octocat/hello-world"
    assert facts.routine_id == "job-1"


def test_pre_turn_handles_failed_tool_result_gracefully() -> None:
    handler = GitHubPrReviewHandler()
    ctx = _StubPreTurnContext(response={"ok": False, "error": {}})
    facts = handler.pre_turn(routine=_routine(), routine_id="job-1", ctx=ctx)
    assert facts.open_prs == []


def _build_facts_for_post_turn(routine: RoutinePayloadV1):
    handler = GitHubPrReviewHandler()
    ctx = _StubPreTurnContext(
        response={
            "ok": True,
            "data": {
                "open_prs": [
                    {"number": 1, "head_sha": "abc", "title": "PR 1"},
                ]
            },
        }
    )
    return handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)


def test_post_turn_success_writes_artifact_and_advances_cursor() -> None:
    handler = GitHubPrReviewHandler()
    routine = _routine()
    facts = _build_facts_for_post_turn(routine)
    sink = _StubSink()
    outcome_text = (
        "<routine_outcome>"
        + json.dumps(
            {
                "reviewed_prs": [
                    {
                        "number": 1,
                        "head_sha_reviewed": "abc",
                        "summary": "looks good",
                        "review_state": "approved_lgtm",
                        "findings": [
                            {
                                "file": "x.py",
                                "line": 1,
                                "severity": "info",
                                "message": "m",
                            }
                        ],
                    }
                ]
            }
        )
        + "</routine_outcome>"
    )
    result = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts,
        outcome_text=outcome_text,
        sink=sink,
    )
    assert result.ok is True
    assert result.artifact_id is not None
    assert len(sink.artifacts) == 1
    assert len(sink.announces) == 1
    assert result.kept_count == 1
    assert result.new_findings_count == 1
    assert result.updated_routine is not None
    cursor = result.updated_routine.cursor
    assert cursor.last_review_per_pr["1"].head_sha == "abc"
    assert 1 in cursor.seen_pr_numbers
    assert cursor.delivered_findings_hashes["1"]
    assert cursor.consecutive_failures == 0


def test_post_turn_dedupes_identical_finding_on_second_run() -> None:
    handler = GitHubPrReviewHandler()
    routine = _routine()
    facts = _build_facts_for_post_turn(routine)
    sink = _StubSink()
    outcome_payload = {
        "reviewed_prs": [
            {
                "number": 1,
                "head_sha_reviewed": "abc",
                "summary": "",
                "findings": [
                    {"file": "x.py", "line": 1, "severity": "info", "message": "m"}
                ],
            }
        ]
    }
    text = f"<routine_outcome>{json.dumps(outcome_payload)}</routine_outcome>"

    first = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts,
        outcome_text=text,
        sink=sink,
    )
    assert first.ok is True
    assert first.new_findings_count == 1
    assert len(sink.artifacts) == 1

    second_routine = first.updated_routine
    facts_2 = _build_facts_for_post_turn(second_routine)
    second = handler.post_turn(
        routine=second_routine,
        routine_id="job-1",
        facts=facts_2,
        outcome_text=text,
        sink=sink,
    )
    assert second.ok is True
    assert second.new_findings_count == 0
    assert len(sink.artifacts) == 1  # unchanged


def test_post_turn_trailer_missing_bumps_failure_counter() -> None:
    handler = GitHubPrReviewHandler()
    routine = _routine()
    facts = _build_facts_for_post_turn(routine)
    sink = _StubSink()
    result = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts,
        outcome_text="model returned plain prose, no trailer here",
        sink=sink,
    )
    assert result.ok is False
    assert result.reason_code == "trailer_missing"
    assert sink.artifacts == []
    assert sink.announces == []
    assert result.updated_routine.cursor.consecutive_failures == 1


def test_post_turn_outcome_validation_failed_records_distinct_code() -> None:
    handler = GitHubPrReviewHandler()
    routine = _routine()
    facts = _build_facts_for_post_turn(routine)
    sink = _StubSink()
    bad_payload = {"reviewed_prs": [{"number": "not-an-int"}]}
    text = f"<routine_outcome>{json.dumps(bad_payload)}</routine_outcome>"
    result = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts,
        outcome_text=text,
        sink=sink,
    )
    assert result.ok is False
    assert result.reason_code == "outcome_validation_failed"
    assert sink.artifacts == []
