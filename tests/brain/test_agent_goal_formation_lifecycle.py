from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _StagedCandidate:
    candidate_id: str
    scope: str
    record_type: str
    title: str
    content: dict[str, Any]
    tags: list[str]
    confidence: float
    meta: dict[str, Any]


class _FakeMemoryApi:
    def __init__(self) -> None:
        self.staged: list[_StagedCandidate] = []
        self._next_id = 0

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any],
        tags: list[str],
        evidence_refs: list[str],
        confidence: float,
        meta: dict[str, Any],
    ) -> str:
        del evidence_refs
        self._next_id += 1
        cid = f"cand-{self._next_id}"
        self.staged.append(
            _StagedCandidate(
                candidate_id=cid,
                scope=scope,
                record_type=record_type,
                title=title,
                content=dict(content),
                tags=list(tags),
                confidence=float(confidence),
                meta=dict(meta),
            )
        )
        return cid

    def search_by_type(self, record_type: str) -> list[_StagedCandidate]:
        return [c for c in self.staged if c.record_type == record_type]


@dataclass
class _FakeProfile:
    agent_id: str = "test-agent"
    goal_execution_policy: str = "suggest"


@dataclass
class _FakeRunner:
    memory_api: _FakeMemoryApi
    profile: _FakeProfile = field(default_factory=_FakeProfile)


@dataclass
class _FakeState:
    memory_candidates: list[str] = field(default_factory=list)


def _make_runner(*, policy: str = "suggest") -> _FakeRunner:
    api = _FakeMemoryApi()
    profile = _FakeProfile(goal_execution_policy=policy)
    return _FakeRunner(memory_api=api, profile=profile)


def test_lifecycle_declare_stage_recall_authorize_default_suggest_policy() -> None:
    from openminion.modules.brain.adapters.context.bridges.memory import (
        _SESSION_START_RECALL_TYPES,
    )
    from openminion.modules.brain.runtime.goal.policy import (
        authorize_goal_action,
    )
    from openminion.modules.brain.runtime.memory import stage_declared_goal
    from openminion.modules.brain.schemas import Decision, GoalDeclaration
    from openminion.modules.retrieve.runtime.retrieval import _candidate_type

    runner = _make_runner(policy="suggest")
    state = _FakeState()

    goal = GoalDeclaration(
        goal="Monitor deployment health daily and report degradation",
        trigger="Recent tool failures when checking deployment status",
        priority="medium",
        action_type="watch",
        suggested_schedule="every 24h",
    )
    decision = Decision(
        route="respond",
        respond_kind="answer",
        confidence=0.9,
        answer="ok",
        goal_declaration=goal,
    )
    assert decision.goal_declaration is goal

    result = stage_declared_goal(runner, state=state, goal=goal)
    assert result["candidate_id"] is not None
    assert result["skipped_reason"] is None
    assert state.memory_candidates == [result["candidate_id"]]
    assert len(runner.memory_api.staged) == 1
    staged = runner.memory_api.staged[0]
    assert staged.record_type == "declared_goal"
    assert "declared_goal" in staged.tags
    assert "action_type:watch" in staged.tags
    assert "priority:medium" in staged.tags
    assert staged.content["goal"] == goal.goal
    assert staged.content["trigger"] == goal.trigger
    assert staged.content["action_type"] == "watch"
    assert staged.confidence == 0.6
    assert staged.scope.startswith("agent:")

    assert "declared_goal" in _SESSION_START_RECALL_TYPES
    recalled = runner.memory_api.search_by_type("declared_goal")
    assert len(recalled) == 1
    assert recalled[0].candidate_id == result["candidate_id"]
    assert _candidate_type(recalled[0].tags) == "declared_goal"

    auth = authorize_goal_action(
        profile_policy=runner.profile.goal_execution_policy,
        action_type=recalled[0].content["action_type"],
    )
    assert auth.allowed is False
    assert auth.requires_user_confirm is True
    assert auth.reason == "policy_suggest"


def test_lifecycle_auto_safe_policy_authorizes_watch_action() -> None:
    from openminion.modules.brain.runtime.goal.policy import (
        authorize_goal_action,
    )
    from openminion.modules.brain.runtime.memory import stage_declared_goal
    from openminion.modules.brain.schemas import GoalDeclaration

    runner = _make_runner(policy="auto_safe")
    state = _FakeState()
    goal = GoalDeclaration(
        goal="X",
        trigger="Y",
        priority="medium",
        action_type="watch",
    )
    stage_declared_goal(runner, state=state, goal=goal)
    auth = authorize_goal_action(
        profile_policy=runner.profile.goal_execution_policy,
        action_type=runner.memory_api.staged[0].content["action_type"],
    )
    assert auth.allowed is True
    assert auth.requires_user_confirm is False
    assert auth.reason == "policy_auto_safe_watch_task"


def test_lifecycle_stage_skipped_when_no_memory_api() -> None:
    from openminion.modules.brain.runtime.memory import stage_declared_goal
    from openminion.modules.brain.schemas import GoalDeclaration

    runner = _FakeRunner(memory_api=None, profile=_FakeProfile())  # type: ignore[arg-type]
    state = _FakeState()
    goal = GoalDeclaration(
        goal="X",
        trigger="Y",
        priority="low",
        action_type="suggest",
    )
    result = stage_declared_goal(runner, state=state, goal=goal)
    assert result == {
        "candidate_id": None,
        "skipped_reason": "memory_api_unavailable",
    }
    assert state.memory_candidates == []


def test_lifecycle_engine_extract_then_stage_round_trip() -> None:
    from openminion.modules.brain.loop.tools.engine import (
        _goal_declaration_payload,
    )
    from openminion.modules.brain.runtime.memory import stage_declared_goal

    class _Response:
        goal_declaration = {
            "goal": "Roundtrip goal",
            "trigger": "Roundtrip trigger",
            "priority": "high",
            "action_type": "task",
            "suggested_schedule": "weekly",
        }

    runner = _make_runner(policy="auto_safe")
    state = _FakeState()
    extracted = _goal_declaration_payload(_Response())
    assert extracted is not None
    assert extracted.priority == "high"
    assert extracted.action_type == "task"
    assert extracted.suggested_schedule == "weekly"

    stage_declared_goal(runner, state=state, goal=extracted)
    assert len(runner.memory_api.staged) == 1
    staged = runner.memory_api.staged[0]
    assert staged.content["suggested_schedule"] == "weekly"
    assert "priority:high" in staged.tags
    assert "action_type:task" in staged.tags
