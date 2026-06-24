from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, get_args

import pytest
from pydantic import ValidationError


def test_goal_revision_in_memory_type() -> None:
    from openminion.modules.memory.models import MemoryType

    assert "goal_revision" in get_args(MemoryType)


def test_goal_revision_validates_required_fields() -> None:
    from openminion.modules.brain.schemas import GoalRevision

    revision = GoalRevision(
        previous_goal="Monitor deployment health daily",
        goal="Escalate only on repeated health-check failures",
        trigger="Single transient failure was resolved cleanly",
        priority="medium",
        action_type="watch",
    )
    assert revision.previous_goal == "Monitor deployment health daily"
    assert revision.goal == "Escalate only on repeated health-check failures"
    assert revision.trigger == "Single transient failure was resolved cleanly"


def test_goal_revision_rejects_missing_previous_goal() -> None:
    from openminion.modules.brain.schemas import GoalRevision

    with pytest.raises(ValidationError):
        GoalRevision(
            previous_goal="",
            goal="Revised goal",
            trigger="Observed counter-evidence",
            priority="medium",
            action_type="suggest",
        )


def test_decision_default_goal_revision_is_none() -> None:
    from openminion.modules.brain.schemas import Decision

    decision = Decision(
        route="respond",
        respond_kind="answer",
        confidence=0.5,
        answer="ok",
    )
    assert decision.goal_revision is None


def test_decision_carries_goal_revision_when_set() -> None:
    from openminion.modules.brain.schemas import Decision, GoalRevision

    revision = GoalRevision(
        previous_goal="Watch deployment health",
        goal="Ask user before creating a watch",
        trigger="Policy requires confirmation",
        priority="low",
        action_type="suggest",
    )
    decision = Decision(
        route="respond",
        respond_kind="answer",
        confidence=0.5,
        answer="ok",
        goal_revision=revision,
    )
    assert decision.goal_revision is revision


def test_goal_revision_extractor_pulls_typed_payload() -> None:
    from openminion.modules.brain.loop.tools.engine import _goal_revision_payload

    class _R:
        goal_revision = {
            "previous_goal": "Monitor deployment health",
            "goal": "Report only repeated failures",
            "trigger": "Transient failure resolved",
            "priority": "low",
            "action_type": "suggest",
        }

    extracted = _goal_revision_payload(_R())
    assert extracted is not None
    assert extracted.previous_goal == "Monitor deployment health"
    assert extracted.goal == "Report only repeated failures"


def test_goal_revision_extractor_returns_none_on_invalid_payload() -> None:
    from openminion.modules.brain.loop.tools.engine import _goal_revision_payload

    class _Bad:
        goal_revision = {"goal": "x", "trigger": "y"}

    assert _goal_revision_payload(_Bad()) is None


def test_outcome_dataclass_has_goal_revision_field() -> None:
    from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopOutcome

    assert "goal_revision" in AdaptiveToolLoopOutcome.__dataclass_fields__


def test_runtime_py_has_no_new_goal_revision_regex() -> None:
    from pathlib import Path
    import re as _re

    runtime_py = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "openminion"
        / "modules"
        / "brain"
        / "loop"
        / "tools"
        / "runtime.py"
    )
    text = runtime_py.read_text(encoding="utf-8")
    assert "_GOAL_REVISION_RE" not in text
    bad_patterns = _re.findall(r"re\.compile\([^)]*goal_revision[^)]*\)", text)
    assert not bad_patterns


def test_goal_revision_in_recall_and_candidate_type_surfaces() -> None:
    from openminion.modules.brain.adapters.context.bridges.memory import (
        _SESSION_START_RECALL_TYPES,
    )
    from openminion.modules.retrieve.runtime.retrieval import _candidate_type

    assert "goal_revision" in _SESSION_START_RECALL_TYPES
    assert _candidate_type(["goal_revision", "priority:medium"]) == "goal_revision"


@dataclass
class _StoredRecord:
    record_id: str
    scope: str
    record_type: str
    title: str
    content: dict[str, Any]
    tags: list[str]


class _FakeMemoryApi:
    def __init__(self) -> None:
        self.records: list[_StoredRecord] = []
        self._next_id = 0

    def put_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any],
        tags: list[str],
        evidence_refs: list[str],
    ) -> str:
        del evidence_refs
        self._next_id += 1
        record_id = f"mem-{self._next_id}"
        self.records.append(
            _StoredRecord(
                record_id=record_id,
                scope=scope,
                record_type=record_type,
                title=title,
                content=dict(content),
                tags=list(tags),
            )
        )
        return record_id


@dataclass
class _FakeProfile:
    agent_id: str = "agent-1"
    goal_execution_policy: str = "suggest"


@dataclass
class _FakeRunner:
    memory_api: _FakeMemoryApi | None
    profile: _FakeProfile = field(default_factory=_FakeProfile)


def _fake_state() -> Any:
    return SimpleNamespace(
        agent_id="agent-1",
        session_id="session-1",
        trace_id="trace-1",
        turn_index=3,
        decision_context_recorded_at="2026-05-08T12:00:00+00:00",
        memory_candidates=[],
    )


def test_stage_goal_revision_authorized_persists_record() -> None:
    from openminion.modules.brain.runtime.memory import stage_goal_revision
    from openminion.modules.brain.schemas import GoalRevision

    runner = _FakeRunner(
        memory_api=_FakeMemoryApi(),
        profile=_FakeProfile(goal_execution_policy="auto_safe"),
    )
    result = stage_goal_revision(
        runner,
        state=_fake_state(),
        goal_revision=GoalRevision(
            previous_goal="Monitor deployment health daily",
            goal="Monitor only after two consecutive failures",
            trigger="Single failure resolved without recurrence",
            priority="medium",
            action_type="watch",
        ),
    )

    assert result["record_id"] is not None
    assert result["skipped_reason"] is None
    assert result["policy_verdict"] == "policy_auto_safe_watch_task"
    assert result["policy_allowed"] is True
    stored = runner.memory_api.records[0]
    assert stored.record_type == "goal_revision"
    assert stored.content["previous_goal"] == "Monitor deployment health daily"
    assert stored.content["goal"] == "Monitor only after two consecutive failures"
    assert "goal_revision" in stored.tags


def test_stage_goal_revision_policy_denied_skips_write() -> None:
    from openminion.modules.brain.runtime.memory import stage_goal_revision
    from openminion.modules.brain.schemas import GoalRevision

    runner = _FakeRunner(
        memory_api=_FakeMemoryApi(),
        profile=_FakeProfile(goal_execution_policy="suggest"),
    )
    result = stage_goal_revision(
        runner,
        state=_fake_state(),
        goal_revision=GoalRevision(
            previous_goal="Create a background watch",
            goal="Keep the same watch goal but ask first",
            trigger="Operator confirmation required",
            priority="medium",
            action_type="watch",
        ),
    )

    assert result["record_id"] is None
    assert result["skipped_reason"] == "policy_denied:policy_suggest"
    assert result["policy_verdict"] == "policy_suggest"
    assert result["policy_allowed"] is False
    assert result["requires_user_confirm"] is True
    assert runner.memory_api.records == []


def test_stage_goal_revision_skips_cleanly_without_memory_api() -> None:
    from openminion.modules.brain.runtime.memory import stage_goal_revision
    from openminion.modules.brain.schemas import GoalRevision

    result = stage_goal_revision(
        _FakeRunner(memory_api=None),
        state=_fake_state(),
        goal_revision=GoalRevision(
            previous_goal="Watch deployment health",
            goal="Suggest watch instead",
            trigger="Harness has no memory api",
            priority="low",
            action_type="suggest",
        ),
    )

    assert result == {
        "record_id": None,
        "skipped_reason": "memory_api_unavailable",
        "policy_verdict": None,
        "policy_allowed": None,
        "requires_user_confirm": None,
    }


def test_goal_revision_bridge_renderer_is_structural() -> None:
    from openminion.modules.brain.adapters.context.bridges.memory import (
        BridgeMemoryClient,
    )

    client = BridgeMemoryClient(backing_store=object())
    item = SimpleNamespace(
        content={
            "previous_goal": "Monitor deployment health daily",
            "goal": "Only escalate after repeated failures",
            "trigger": "Single transient failure resolved",
            "priority": "medium",
            "action_type": "watch",
            "policy_verdict": "policy_auto_safe_watch_task",
        }
    )

    text = client._render_record_text(item, record_type="goal_revision")  # noqa: SLF001
    assert "previous_goal=Monitor deployment health daily" in text
    assert "goal=Only escalate after repeated failures" in text
    assert "policy_verdict=policy_auto_safe_watch_task" in text
