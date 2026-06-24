from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.github.interfaces import (
    TOOL_GITHUB_FETCH_CHECKS,
    TOOL_GITHUB_FETCH_COMMENTS,
    TOOL_GITHUB_FETCH_DIFF,
    TOOL_GITHUB_FETCH_PR,
    TOOL_GITHUB_LIST_PRS,
)
from openminion.tools.github.plugin import register as register_github_tools
from openminion.tools.github.providers import (
    provider_registry,
    register_provider,
)
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY
from openminion.tools.task.routine.dispatcher import (
    GitHubPrReviewHandler,
    PostTurnSink,
    PreTurnContext,
    build_default_dispatcher,
)
from openminion.tools.task.routine.schemas import (
    GitHubPrReviewConfigV1,
    RoutinePayloadV1,
)


class _FakeGithubProvider:
    provider_id = "openminion-builtin-github"

    def __init__(self) -> None:
        self.pr_table: dict[int, dict[str, Any]] = {}
        self.audit: list[tuple[str, dict[str, Any]]] = []

    def set_prs(self, prs: list[dict[str, Any]]) -> None:
        self.pr_table = {pr["number"]: pr for pr in prs}

    def list_prs(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        self.audit.append(("list_prs", dict(args)))
        return {
            "ok": True,
            "data": {"open_prs": list(self.pr_table.values())},
        }

    def fetch_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        self.audit.append(("fetch_pr", dict(args)))
        pr = self.pr_table.get(args["number"], {})
        return {"ok": True, "data": pr}

    def fetch_diff(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return {"ok": True, "data": {"diff": ""}}

    def fetch_comments(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return {"ok": True, "data": {"comments": []}}

    def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return {"ok": True, "data": {"checks": []}}

    def healthcheck(self) -> bool:
        return True


class _RuntimeBackedPreTurnContext(PreTurnContext):
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self.audit: list[tuple[str, dict[str, Any]]] = []

    def invoke_tool(self, *, name: str, args: Mapping[str, Any]) -> Mapping[str, Any]:
        spec = self._registry.list().get(name)
        if spec is None:
            raise AssertionError(f"tool {name!r} not registered")
        self.audit.append((name, dict(args)))
        return spec.handler(dict(args), ctx=None)


class _RecordingSink(PostTurnSink):
    def __init__(self) -> None:
        self.artifacts: list[tuple[str, str]] = []
        self.announces: list[tuple[str, str]] = []

    def write_artifact(self, *, routine_id: str, body: str) -> str:
        artifact_id = f"artifact://{routine_id}/{len(self.artifacts)}"
        self.artifacts.append((artifact_id, body))
        return artifact_id

    def announce(self, *, routine_id: str, summary: str) -> None:
        self.announces.append((routine_id, summary))


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_github_tools(reg)
    return reg


@pytest.fixture
def fake_provider() -> _FakeGithubProvider:
    provider_registry().reset()
    provider = _FakeGithubProvider()
    register_provider(provider)
    yield provider
    provider_registry().reset()


def _initial_routine() -> RoutinePayloadV1:
    return RoutinePayloadV1(
        config=GitHubPrReviewConfigV1(owner="octocat", repo="hello-world")
    )


def _outcome_text(reviewed_prs: list[dict[str, Any]]) -> str:
    payload = json.dumps({"reviewed_prs": reviewed_prs})
    return f"<routine_outcome>{payload}</routine_outcome>"


def test_brpr_08_four_tick_deterministic_e2e(
    registry: ToolRegistry, fake_provider: _FakeGithubProvider
) -> None:
    handler = GitHubPrReviewHandler()
    sink = _RecordingSink()
    ctx = _RuntimeBackedPreTurnContext(registry)
    routine = _initial_routine()

    fake_provider.set_prs(
        [
            {
                "number": 42,
                "head_sha": "sha-v1",
                "title": "Add feature X",
                "lines_added": 100,
                "lines_deleted": 5,
            }
        ]
    )
    facts_1 = handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)
    assert ctx.audit[-1] == (
        TOOL_GITHUB_LIST_PRS,
        {"owner": "octocat", "repo": "hello-world", "state": "open"},
    )
    assert len(facts_1.open_prs) == 1
    assert facts_1.open_prs[0].number == 42
    assert facts_1.newly_opened_prs == [42]

    out_1 = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts_1,
        outcome_text=_outcome_text(
            [
                {
                    "number": 42,
                    "head_sha_reviewed": "sha-v1",
                    "review_state": "needs_human_review",
                    "summary": "First review pass.",
                    "findings": [
                        {
                            "file": "x.py",
                            "line": 10,
                            "severity": "warn",
                            "message": "missing tests",
                        }
                    ],
                }
            ]
        ),
        sink=sink,
    )
    assert out_1.ok is True
    assert out_1.kept_count == 1
    assert out_1.new_findings_count == 1
    assert len(sink.artifacts) == 1
    assert len(sink.announces) == 1
    routine = out_1.updated_routine
    assert routine.cursor.last_review_per_pr["42"].head_sha == "sha-v1"
    assert 42 in routine.cursor.seen_pr_numbers
    assert routine.cursor.consecutive_failures == 0

    facts_2 = handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)
    assert facts_2.open_prs == []
    out_2 = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts_2,
        outcome_text=_outcome_text([]),  # model emits empty list correctly
        sink=sink,
    )
    assert out_2.ok is True
    assert out_2.new_findings_count == 0
    assert len(sink.artifacts) == 1
    assert len(sink.announces) == 1
    routine = out_2.updated_routine
    assert routine.cursor.last_check_iso == facts_2.checked_at
    assert routine.cursor.last_review_per_pr["42"].head_sha == "sha-v1"

    fake_provider.set_prs(
        [
            {
                "number": 42,
                "head_sha": "sha-v2",
                "title": "Add feature X",
                "lines_added": 110,
                "lines_deleted": 5,
            }
        ]
    )
    facts_3 = handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)
    assert len(facts_3.open_prs) == 1
    assert facts_3.open_prs[0].head_sha == "sha-v2"
    assert facts_3.open_prs[0].last_review_sha == "sha-v1"

    out_3 = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts_3,
        outcome_text=_outcome_text(
            [
                {
                    "number": 42,
                    "head_sha_reviewed": "sha-v2",
                    "review_state": "approved_lgtm",
                    "summary": "Tests added; LGTM.",
                    "findings": [],
                }
            ]
        ),
        sink=sink,
    )
    assert out_3.ok is True
    assert out_3.kept_count == 1
    assert len(sink.artifacts) == 2
    assert len(sink.announces) == 2
    routine = out_3.updated_routine
    assert routine.cursor.last_review_per_pr["42"].head_sha == "sha-v2"

    fake_provider.set_prs(
        [
            {
                "number": 99,
                "head_sha": "brand-new",
                "title": "another PR",
            }
        ]
    )
    facts_4 = handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)
    out_4 = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts_4,
        outcome_text="model returned only prose, no <routine_outcome> here",
        sink=sink,
    )
    assert out_4.ok is False
    assert out_4.reason_code == "trailer_missing"
    assert len(sink.artifacts) == 2
    routine = out_4.updated_routine
    assert routine.cursor.consecutive_failures == 1


def test_brpr_08_pre_turn_calls_through_tool_runtime_only(
    registry: ToolRegistry, fake_provider: _FakeGithubProvider
) -> None:
    handler = GitHubPrReviewHandler()
    sink = _RecordingSink()
    ctx = _RuntimeBackedPreTurnContext(registry)
    routine = _initial_routine()
    fake_provider.set_prs([{"number": 1, "head_sha": "a"}])

    facts = handler.pre_turn(routine=routine, routine_id="job-1", ctx=ctx)
    out = handler.post_turn(
        routine=routine,
        routine_id="job-1",
        facts=facts,
        outcome_text=_outcome_text(
            [
                {
                    "number": 1,
                    "head_sha_reviewed": "a",
                    "summary": "ok",
                    "findings": [],
                }
            ]
        ),
        sink=sink,
    )
    assert out.ok is True

    audit_names = [name for name, _args in ctx.audit]
    assert TOOL_GITHUB_LIST_PRS in audit_names
    for unused in (
        TOOL_GITHUB_FETCH_PR,
        TOOL_GITHUB_FETCH_DIFF,
        TOOL_GITHUB_FETCH_COMMENTS,
        TOOL_GITHUB_FETCH_CHECKS,
    ):
        assert unused not in audit_names


def test_brpr_08_plain_watch_payload_is_ignored_by_dispatcher() -> None:
    dispatcher = build_default_dispatcher()
    plain_payload = {
        "kind": "agentTurn",
        WATCH_PAYLOAD_KEY: {
            "description": "plain watch",
            "alert_condition": "always",
            "interval_minutes": 5,
        },
    }
    assert dispatcher.is_routine_payload(plain_payload) is False
    assert dispatcher.routine_for(plain_payload) is None
