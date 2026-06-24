from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openminion.services.brain.post_execution import BrainBridgeTurnMixin
from openminion.services.brain.post_execution.contracts import _MissionResetPreview
from openminion.services.brain.post_execution.reset import (
    BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT,
    _apply_mission_reset_preview,
    _apply_plan_and_goal_reset,
)


class _DummyBridge(BrainBridgeTurnMixin):
    pass


class _DummySessionAPI:
    def __init__(self, state: dict[str, Any]) -> None:
        self._state = dict(state)
        self.written: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []

    def get_latest_working_state(self, session_id: str) -> dict[str, Any]:
        del session_id
        return dict(self._state)

    def put_working_state(
        self, session_id: str, *, state_inline: dict[str, Any]
    ) -> None:
        del session_id
        self.written = dict(state_inline)

    def append_event(
        self,
        session_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
        **_kwargs: Any,
    ) -> str:
        self.events.append(
            {
                "session_id": session_id,
                "type": type,
                "payload": dict(payload),
                "trace_id": trace_id,
            }
        )
        return f"{session_id}-event-{len(self.events)}"


class _DummyPolicyAPI:
    def __init__(self) -> None:
        from openminion.modules.policy.runtime.service import (
            parse_confirmation_response,
        )

        self._delegate = parse_confirmation_response

    def parse_confirmation_response(self, text: str) -> str:
        return str(self._delegate(text)).strip().lower()


class _DummyRunner:
    def __init__(self, state: dict[str, Any], *, with_policy: bool = True) -> None:
        self.session_api = _DummySessionAPI(state)
        self.policy_api = _DummyPolicyAPI() if with_policy else None
        self.profile = SimpleNamespace(
            budgets=SimpleNamespace(
                max_ticks_per_user_turn=8,
                max_tool_calls=8,
                max_a2a_calls=0,
                max_total_llm_tokens=100000,
                max_elapsed_ms=45000,
            )
        )


_PENDING_COMMAND = SimpleNamespace(
    command_id="cmd-file-write-1",
    kind="tool",
    inputs={"path": "./tmp-bbpc/README.md", "body": "hi"},
)


def _state_with_pending(*, pending: Any = _PENDING_COMMAND) -> dict[str, Any]:
    return {
        "status": "waiting_user",
        "phase": "ACT",
        "goal": "write a small README",
        "pending_confirmation_command": pending,
        "pending_confirmation_sub_intents": ["intent-1"],
        "pending_confirmation_sub_intent_refs": ["ref-1"],
        "pending_confirmation_rationale": "user asked to write a file",
        "pending_confirmation_success_criteria": {"file_exists": True},
        "pending_confirmation_feasibility_state": {"awaiting_user_choice": False},
        "pending_confirmation_feasibility_report": {"summary": "fine"},
        "plan": {"steps": [{"kind": "tool", "tool": "file.write"}]},
        "cursor": 0,
        "constraints": [],
        "decision_feasibility_state": {},
        "awaiting_continuation_reply": False,
        "continuation_guard_command_signature": "",
    }


def _run_reset(
    *, runner: _DummyRunner, user_input: str, session_id: str = "sess-bbpc"
) -> dict[str, Any] | None:
    bridge = _DummyBridge()
    bridge._reset_state_for_new_input(
        runner=runner, session_id=session_id, user_input=user_input
    )
    return runner.session_api.written


# preservation behavior


# Inputs the canonical `policy.service.parse_confirmation_response`
# classifies as `affirm` or `deny`. Verified against the real parser to
# avoid mirroring its vocabulary in this test file.
_PARSED_AFFIRM_INPUTS = ["yes", "Y", "YES", "y", "proceed", "confirm"]
_PARSED_DENY_INPUTS = ["no", "n", "abort", "stop"]
# the canonical parser classifies it as `deny`, so both gates fire.
# Inputs the canonical parser classifies as `unclear` (NOT affirm/deny).
# These should fall through and wipe the pending command.
_PARSED_UNCLEAR_INPUTS = [
    "yeah",
    "yep",
    "nope",
    "ok",
    "okay",
    "deny",
    "approve",
    "go ahead",
]


@pytest.mark.parametrize("user_input", _PARSED_AFFIRM_INPUTS + _PARSED_DENY_INPUTS)
def test_pending_confirmation_preserved_on_parsed_affirm_or_deny(
    user_input: str,
) -> None:
    runner = _DummyRunner(_state_with_pending())
    written = _run_reset(runner=runner, user_input=user_input)
    assert written is not None
    assert written["pending_confirmation_command"] is _PENDING_COMMAND
    assert written["pending_confirmation_sub_intents"] == ["intent-1"]
    assert written["pending_confirmation_sub_intent_refs"] == ["ref-1"]
    assert written["pending_confirmation_rationale"] == "user asked to write a file"
    assert written["pending_confirmation_success_criteria"] == {"file_exists": True}
    assert written["pending_confirmation_feasibility_state"] == {
        "awaiting_user_choice": False
    }
    assert written["pending_confirmation_feasibility_report"] == {"summary": "fine"}


def test_pending_confirmation_preserved_on_cancel_via_both_paths() -> None:
    runner = _DummyRunner(_state_with_pending())
    written = _run_reset(runner=runner, user_input="cancel")
    assert written is not None
    assert written["pending_confirmation_command"] is _PENDING_COMMAND
    bbpc_events = _events_of_type(
        runner, BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT
    )
    assert len(bbpc_events) == 1
    assert bbpc_events[0]["payload"] == {"reply": "deny", "command_kind": "tool"}


@pytest.mark.parametrize(
    "user_input",
    ["tell me a joke", "what time is it", "hello", "design CRM SaaS"]
    + _PARSED_UNCLEAR_INPUTS,
)
def test_pending_confirmation_wiped_on_unrelated_input(user_input: str) -> None:
    runner = _DummyRunner(_state_with_pending())
    written = _run_reset(runner=runner, user_input=user_input)
    assert written is not None
    assert written["pending_confirmation_command"] is None
    assert written["pending_confirmation_sub_intents"] == []
    assert written["pending_confirmation_sub_intent_refs"] == []
    assert written["pending_confirmation_rationale"] == ""
    assert written["pending_confirmation_success_criteria"] == {}
    assert written["pending_confirmation_feasibility_state"] == {}
    assert written["pending_confirmation_feasibility_report"] is None


def test_no_pending_command_baseline_is_no_op() -> None:
    state = _state_with_pending(pending=None)
    runner = _DummyRunner(state)
    written = _run_reset(runner=runner, user_input="yes")
    assert written is not None
    assert written["pending_confirmation_command"] is None
    # No event should have been emitted because there was no pending
    # command to preserve.
    assert runner.session_api.events == []


def test_pending_command_preserved_when_runner_has_no_policy_api() -> None:
    state = _state_with_pending()
    runner = _DummyRunner(state, with_policy=False)
    written = _run_reset(runner=runner, user_input="yes")
    assert written is not None
    assert written["pending_confirmation_command"] is _PENDING_COMMAND


# telemetry event behavior


def _events_of_type(runner: _DummyRunner, event_type: str) -> list[dict[str, Any]]:
    return [event for event in runner.session_api.events if event["type"] == event_type]


@pytest.mark.parametrize(
    "user_input,expected_reply",
    [
        ("yes", "affirm"),
        ("no", "deny"),
        ("proceed", "affirm"),
        ("cancel", "deny"),
        ("abort", "deny"),
    ],
)
def test_telemetry_event_emitted_on_parser_driven_preservation(
    user_input: str, expected_reply: str
) -> None:
    runner = _DummyRunner(_state_with_pending())
    _run_reset(runner=runner, user_input=user_input)
    events = _events_of_type(runner, BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT)
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload == {"reply": expected_reply, "command_kind": "tool"}


def test_telemetry_event_not_emitted_on_unrelated_input() -> None:
    runner = _DummyRunner(_state_with_pending())
    _run_reset(runner=runner, user_input="hello")
    assert (
        _events_of_type(runner, BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT) == []
    )


def test_telemetry_event_not_emitted_when_no_pending_command() -> None:
    state = _state_with_pending(pending=None)
    runner = _DummyRunner(state)
    _run_reset(runner=runner, user_input="yes")
    assert (
        _events_of_type(runner, BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT) == []
    )


def test_telemetry_event_payload_handles_dict_pending_command() -> None:
    state = _state_with_pending(
        pending={"command_id": "cmd-1", "kind": "tool", "inputs": {}}
    )
    runner = _DummyRunner(state)
    _run_reset(runner=runner, user_input="yes")
    events = _events_of_type(runner, BRAIN_BRIDGE_CONFIRMATION_RESET_PRESERVED_EVENT)
    assert len(events) == 1
    assert events[0]["payload"]["command_kind"] == "tool"


def test_mission_reset_preview_alias_matches_runtime_active_flag() -> None:
    preview = _MissionResetPreview(
        parsed_state=None,
        mission_runtime_active=True,
        route_action="ordinary",
        route_objective="",
        route_fork_input="",
    )

    assert preview.mission_active is True


def test_apply_plan_and_goal_reset_handles_inactive_preview_without_attr_error() -> (
    None
):
    updated: dict[str, Any] = {}
    _apply_plan_and_goal_reset(
        updated=updated,
        state_inline={},
        user_input="hello",
        preservation=SimpleNamespace(
            preserve_existing_plan=False,
            previous_goal="",
            preserve_followup_goal=False,
        ),
        mission_preview=_MissionResetPreview(
            parsed_state=None,
            mission_runtime_active=False,
            route_action="ordinary",
            route_objective="",
            route_fork_input="",
        ),
    )

    assert updated["goal"] == "hello"


def test_apply_mission_reset_preview_handles_inactive_preview_without_attr_error() -> (
    None
):
    updated: dict[str, Any] = {}
    _apply_mission_reset_preview(
        updated=updated,
        runner=_DummyRunner({}),
        mission_preview=_MissionResetPreview(
            parsed_state=None,
            mission_runtime_active=False,
            route_action="ordinary",
            route_objective="",
            route_fork_input="",
        ),
    )

    assert updated == {}
