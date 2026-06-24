from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.constants import (
    RESPOND_KIND_ASSISTANT,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.brain.meta.bridge import respond_with_meta
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    StepOutput,
    WorkingState,
)
from openminion.modules.brain.schemas.state import ActionError
from openminion.modules.brain.state import respond


class _RecordingSessionAPI:
    def __init__(self) -> None:
        self.turns: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.status_updates: list[tuple[str, str]] = []
        self.working_states: list[tuple[str, dict[str, Any]]] = []

    def append_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        **kwargs: Any,
    ) -> str:
        self.turns.append(
            {
                "session_id": session_id,
                "role": role,
                "content": content,
                "kwargs": kwargs,
            }
        )
        return f"turn-{len(self.turns)}"

    def append_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> str:
        del kwargs
        self.events.append(
            {
                "session_id": session_id,
                "type": event_type,
                "payload": payload,
            }
        )
        return f"event-{len(self.events)}"

    def update_session_status(self, session_id: str, status: str) -> None:
        self.status_updates.append((session_id, status))

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        del session_id
        return []

    def put_working_state(
        self, session_id: str, *, state_inline: dict[str, Any] | None = None, **_kwargs
    ) -> None:
        self.working_states.append((session_id, state_inline or {}))


class _DummyLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, payload: dict[str, Any], **kwargs: Any) -> None:
        del kwargs
        self.events.append((name, payload))


def _state(*, status: str = "waiting_user") -> WorkingState:
    return WorkingState(
        session_id="s-pchc",
        agent_id="agent-pchc",
        budgets_remaining=BudgetCounters(
            ticks=4,
            tool_calls=4,
            a2a_calls=0,
            tokens=1000,
            time_ms=60_000,
        ),
        status=status,
    )


def _runner(session_api: _RecordingSessionAPI) -> SimpleNamespace:
    compact_calls: list[str] = []

    def _compact(*, state, logger, content):
        del state, logger
        compact_calls.append(content)

    runner = SimpleNamespace(
        session_api=session_api,
        context_api=None,
        skill_api=None,
        profile=None,
        meta_engine=None,
        _compact=_compact,
        _emit_phase_status=lambda **_kwargs: None,
        _compact_calls=compact_calls,
    )
    return runner


def test_respond_default_kind_appends_assistant_turn() -> None:
    session_api = _RecordingSessionAPI()
    runner = _runner(session_api)
    logger = _DummyLogger()
    state = _state(status="active")

    out = respond(
        runner,
        state=state,
        logger=logger,
        message="Hello operator, here is my answer.",
        status="done",
    )

    assert isinstance(out, StepOutput)
    assert out.message == "Hello operator, here is my answer."
    assert len(session_api.turns) == 1
    assert session_api.turns[0]["role"] == "assistant"
    assert session_api.turns[0]["content"] == "Hello operator, here is my answer."
    assert session_api.events == []
    assert runner._compact_calls == ["Hello operator, here is my answer."]


def test_respond_default_kind_value_is_assistant() -> None:
    assert RESPOND_KIND_ASSISTANT == "assistant"


def test_respond_policy_confirmation_kind_skips_assistant_turn() -> None:
    session_api = _RecordingSessionAPI()
    runner = _runner(session_api)
    logger = _DummyLogger()
    state = _state(status="waiting_user")

    prose = (
        "Policy confirmation required.\n"
        "file.write (path=hello.txt)\n"
        "Reply exactly yes to confirm or exactly no to cancel."
    )

    out = respond(
        runner,
        state=state,
        logger=logger,
        message=prose,
        status="waiting_user",
        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    )

    assert isinstance(out, StepOutput)
    assert out.message == prose

    assert session_api.turns == []

    assert len(session_api.events) == 1
    event = session_api.events[0]
    assert event["type"] == SESSION_EVENT_POLICY_CONFIRMATION_PROMPT
    assert event["payload"]["message"] == prose
    assert event["payload"]["status"] == "waiting_user"
    assert event["payload"]["is_error"] is False

    assert runner._compact_calls == []


def test_respond_policy_confirmation_kind_writes_event_even_for_broad_tools() -> None:
    session_api = _RecordingSessionAPI()
    runner = _runner(session_api)
    logger = _DummyLogger()

    for tool_name, args_preview in (
        ("file.write", "path=hello.txt"),
        ("file.delete", "path=/tmp/x"),
        ("exec.run", "cmd=rm -rf .cache"),
    ):
        prose = (
            "Policy confirmation required.\n"
            f"{tool_name} ({args_preview})\n"
            "Reply exactly yes to confirm or exactly no to cancel."
        )
        state = _state(status="waiting_user")
        respond(
            runner,
            state=state,
            logger=logger,
            message=prose,
            status="waiting_user",
            kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
        )

    assert session_api.turns == []
    assert [event["type"] for event in session_api.events] == [
        SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
        SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
        SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
    ]


def test_respond_rejects_unknown_kind() -> None:
    session_api = _RecordingSessionAPI()
    runner = _runner(session_api)
    logger = _DummyLogger()
    state = _state()

    try:
        respond(
            runner,
            state=state,
            logger=logger,
            message="x",
            status="active",
            kind="not_a_real_kind",  # type: ignore[arg-type]
        )
    except ValueError as exc:
        assert "not_a_real_kind" in str(exc)
    else:
        raise AssertionError("respond() should reject unknown kind")


def test_respond_policy_confirmation_kind_skips_append_event_if_unsupported() -> None:

    class _LegacyAPI(_RecordingSessionAPI):
        append_event = None  # type: ignore[assignment]

    session_api = _LegacyAPI()
    runner = _runner(session_api)
    logger = _DummyLogger()
    state = _state(status="waiting_user")

    out = respond(
        runner,
        state=state,
        logger=logger,
        message="Reply exactly yes to confirm or exactly no to cancel.",
        status="waiting_user",
        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    )

    assert isinstance(out, StepOutput)
    assert session_api.turns == []
    assert session_api.events == []


def test_respond_with_meta_threads_kind_to_runner_respond() -> None:
    captured: dict[str, Any] = {}

    def _fake_respond(*, state, logger, message, status, action_result, kind):
        del state, logger
        captured.update(
            {
                "message": message,
                "status": status,
                "action_result": action_result,
                "kind": kind,
            }
        )
        return StepOutput(
            session_id="s-meta",
            status=status,
            message=message,
            working_state=_state(status=status),
            action_result=action_result,
        )

    runner = SimpleNamespace(
        meta_engine=None,
        meta_api=None,
        _meta_overrides=None,
        options=SimpleNamespace(metactl_enabled=False),
        _respond=_fake_respond,
    )
    logger = _DummyLogger()
    state = _state(status="waiting_user")

    out = respond_with_meta(
        runner,
        state=state,
        logger=logger,
        message="prose",
        status="waiting_user",
        action_result=None,
        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    )

    assert isinstance(out, StepOutput)
    assert captured["kind"] == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
    assert captured["message"] == "prose"
    assert captured["status"] == "waiting_user"


def test_respond_with_meta_defaults_kind_to_assistant() -> None:
    captured: dict[str, Any] = {}

    def _fake_respond(*, state, logger, message, status, action_result, kind):
        del state, logger
        captured["kind"] = kind
        return StepOutput(
            session_id="s-meta",
            status=status,
            message=message,
            working_state=_state(status=status),
            action_result=action_result,
        )

    runner = SimpleNamespace(
        meta_engine=None,
        meta_api=None,
        _meta_overrides=None,
        options=SimpleNamespace(metactl_enabled=False),
        _respond=_fake_respond,
    )
    logger = _DummyLogger()
    state = _state(status="active")

    respond_with_meta(
        runner,
        state=state,
        logger=logger,
        message="hello",
        status="done",
    )

    assert captured["kind"] == RESPOND_KIND_ASSISTANT


def test_full_confirmation_cycle_leaves_no_policy_prose_in_transcript() -> None:
    session_api = _RecordingSessionAPI()
    runner = _runner(session_api)
    logger = _DummyLogger()

    session_api.append_turn(
        "s-pchc", "user", "Please write hello.txt", meta={"ts": "t0"}
    )

    state = _state(status="waiting_user")
    prose = (
        "Policy confirmation required.\n"
        "file.write (path=hello.txt)\n"
        "Reply exactly yes to confirm or exactly no to cancel."
    )
    respond(
        runner,
        state=state,
        logger=logger,
        message=prose,
        status="waiting_user",
        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    )

    session_api.append_turn("s-pchc", "user", "yes", meta={"ts": "t2"})

    state2 = _state(status="active")
    respond(
        runner,
        state=state2,
        logger=logger,
        message="Wrote hello.txt successfully.",
        status="done",
    )

    transcript_roles = [t["role"] for t in session_api.turns]
    assert transcript_roles == ["user", "user", "assistant"]
    transcript_contents = [t["content"] for t in session_api.turns]
    assert "Policy confirmation required." not in " | ".join(transcript_contents)
    assert any(
        e["type"] == SESSION_EVENT_POLICY_CONFIRMATION_PROMPT
        for e in session_api.events
    )


def test_action_result_error_propagates_into_event_payload() -> None:
    session_api = _RecordingSessionAPI()
    runner = _runner(session_api)
    logger = _DummyLogger()
    state = _state(status="waiting_user")

    action_result = ActionResult(
        command_id="cmd-1",
        status="needs_user",
        error=ActionError(code="confirm_required", message="needs confirm"),
    )

    respond(
        runner,
        state=state,
        logger=logger,
        message="Reply exactly yes to confirm or exactly no to cancel.",
        status="waiting_user",
        action_result=action_result,
        kind=RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    )

    assert len(session_api.events) == 1
    assert session_api.events[0]["payload"]["is_error"] is True
