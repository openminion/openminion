from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    WorkingState,
)
from openminion.modules.brain.state import respond


class _DummySessionAPI:
    def append_turn(self, *args, **kwargs) -> None:  # noqa: ARG002
        return

    def update_session_status(self, *args, **kwargs) -> None:  # noqa: ARG002
        return

    def list_turns(self, *args, **kwargs) -> list[dict]:  # noqa: ARG002
        return []

    def update_summary(self, *args, **kwargs) -> None:  # noqa: ARG002
        return

    def put_working_state(self, *args, **kwargs) -> None:  # noqa: ARG002
        return


class _DummyLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, name: str, payload: dict, **kwargs) -> None:  # noqa: ARG002
        self.events.append((name, payload))


class _RecordingSkillAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def log_run(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return "run-id"


class _FailingSkillAPI:
    def log_run(self, **kwargs) -> str:  # noqa: ARG002
        raise RuntimeError("boom")


def _state(
    *, skill_id: str | None, version_hash: str | None, phase: str
) -> WorkingState:
    return WorkingState(
        session_id="session-1",
        agent_id="agent-1",
        active_skill_id=skill_id,
        active_skill_version_hash=version_hash,
        phase=phase,
        budgets_remaining=BudgetCounters(
            ticks=8,
            tool_calls=8,
            a2a_calls=0,
            tokens=1000,
            time_ms=60000,
        ),
    )


def _runner(skill_api) -> SimpleNamespace:
    return SimpleNamespace(
        session_api=_DummySessionAPI(),
        context_api=None,
        skill_api=skill_api,
        _compact=lambda **_: None,
    )


def test_skill_log_run_success_and_state_clear() -> None:
    skill_api = _RecordingSkillAPI()
    runner = _runner(skill_api)
    logger = _DummyLogger()
    state = _state(skill_id="skill-alpha", version_hash="v1", phase="ACT")

    respond(
        runner,
        state=state,
        logger=logger,
        message="ok",
        status="waiting_user",
    )

    assert len(skill_api.calls) == 1
    call = skill_api.calls[0]
    assert call["session_id"] == "session-1"
    assert call["agent_id"] == "agent-1"
    assert call["skill_id"] == "skill-alpha"
    assert call["version_hash"] == "v1"
    assert call["used_for"] == "act"
    assert call["outcome"] == "success"
    assert state.active_skill_id is None
    assert state.active_skill_version_hash is None


def test_skill_log_run_strict_guard_requires_both_fields() -> None:
    skill_api = _RecordingSkillAPI()
    runner = _runner(skill_api)
    logger = _DummyLogger()
    state = _state(skill_id="skill-alpha", version_hash=None, phase="PLAN")

    respond(
        runner,
        state=state,
        logger=logger,
        message="ok",
        status="waiting_user",
    )

    assert skill_api.calls == []
    assert state.active_skill_id is None
    assert state.active_skill_version_hash is None


def test_skill_log_run_exception_is_non_fatal_and_emits_event() -> None:
    runner = _runner(_FailingSkillAPI())
    logger = _DummyLogger()
    state = _state(skill_id="skill-alpha", version_hash="v1", phase="PLAN")

    result = respond(
        runner,
        state=state,
        logger=logger,
        message="ok",
        status="waiting_user",
    )

    assert result.status == "waiting_user"
    assert any(name == "skill.log_run.failed" for name, _ in logger.events)
    assert state.active_skill_id is None
    assert state.active_skill_version_hash is None


def test_skill_log_run_outcome_maps_failed_action_result() -> None:
    skill_api = _RecordingSkillAPI()
    runner = _runner(skill_api)
    logger = _DummyLogger()
    state = _state(skill_id="skill-alpha", version_hash="v1", phase="VERIFY")
    action_result = ActionResult(command_id="cmd-1", status="failed")

    respond(
        runner,
        state=state,
        logger=logger,
        message="failed",
        status="error",
        action_result=action_result,
    )

    assert len(skill_api.calls) == 1
    call = skill_api.calls[0]
    assert call["used_for"] == "verify"
    assert call["outcome"] == "fail"
