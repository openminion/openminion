"""The owners of a turn's capture authorization (#448).

- `memory_capture_allowed` asks the same `memory.write` check as
  `memory_capture_recovery_allowed`, with the turn's context;
- the brain bridge decides once per turn from the inbound message;
- the runner does not extract or write a capture bundle without that decision.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from openminion.base.types import Message
from openminion.modules.brain.runner.coordinator import BrainRunner
from openminion.modules.brain.runner.tick.input_processing import (
    _capture_user_message,
)
from openminion.modules.policy import SecurityPolicyEngine
from openminion.modules.policy.adapters import memory as capture_policy
from openminion.modules.policy.runtime.security import default_internal_actor
from openminion.modules.session.capture import (
    build_capture_identity,
    capture_identity_metadata,
)
from openminion.modules.tool import ToolRegistry, ToolSpec
from openminion.services.brain.post_execution import BrainBridgeTurnMixin

CAPTURE_ITEM = {
    "kind": "user_preference",
    "normalized_key": "user_preference:indentation",
    "title": "Indentation",
    "content": "The user prefers tabs.",
}


class _NoArgs(BaseModel):
    pass


def _tools(min_scope: str = "WRITE_SAFE") -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="memory.write",
            args_model=_NoArgs,
            min_scope=min_scope,
            handler=lambda _args, _ctx: {},
        )
    )
    return tools


class _RecordingPolicy(SecurityPolicyEngine):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.checks: list[Any] = []
        self.decisions: list[Any] = []

    def evaluate(self, check: Any) -> Any:
        decision = super().evaluate(check)
        self.checks.append(check)
        self.decisions.append(decision)
        return decision


def _both(policy: Any, tools: Any) -> tuple[bool, bool]:
    turn = capture_policy.memory_capture_allowed(
        "session-1",
        "root-1",
        channel="console",
        policy=policy,
        tools=tools,
        agent_id="agent-a",
    )
    recovery = capture_policy.memory_capture_recovery_allowed(
        "session-1",
        "root-1",
        "turn.outcome:1",
        "capture:1",
        policy=policy,
        tools=tools,
        agent_id="agent-a",
    )
    return turn, recovery


@pytest.mark.parametrize(
    ("constraints", "min_scope", "decision", "reason"),
    [
        ([], "WRITE_SAFE", "allow", "allowed"),
        (["NEVER reveal secrets"], "WRITE_SAFE", "allow", "allowed"),
        (
            ["NEVER write memory records"],
            "WRITE_SAFE",
            "require_approval",
            "identity_hard_constraint_restricted",
        ),
        ([], "POWER_USER", "deny", "missing_tool_scope"),
    ],
)
def test_turn_and_recovery_capture_ask_the_same_memory_write_check(
    constraints: list[str], min_scope: str, decision: str, reason: str
) -> None:
    # The configured internal actor scope reaches neither capture check.
    policy = _RecordingPolicy(
        identity_constraints=constraints,
        internal_actor_scopes=frozenset({"tool.execute.elevated"}),
    )

    allowed = _both(policy, _tools(min_scope))

    assert allowed == (decision == "allow", decision == "allow")
    turn, recovery = policy.checks
    assert turn.actor == recovery.actor == default_internal_actor("agent-a")
    assert turn.action == recovery.action
    assert (turn.action.resource, turn.action.verb, turn.action.tool_name) == (
        "tool",
        "execute",
        "memory.write",
    )
    assert (turn.context.channel, turn.context.target) == ("console", "memory-capture")
    assert (recovery.context.channel, recovery.context.target) == (
        "cron",
        "memory-capture-recovery",
    )
    for check in policy.checks:
        assert (check.context.session_id, check.context.run_id) == (
            "session-1",
            "root-1",
        )
    assert [(d.decision, d.reason_code) for d in policy.decisions] == [
        (decision, reason)
    ] * 2


def test_capture_checks_keep_the_recovery_rule_without_policy_or_tools() -> None:
    policy = _RecordingPolicy()

    assert _both(None, _tools()) == (True, True)
    assert _both(policy, None) == (False, False)
    assert policy.checks == []


class _Bridge(BrainBridgeTurnMixin):
    def __init__(self, *, policy: Any, tools: Any) -> None:
        self._security_policy = policy
        self._tools = tools
        self._identity_agent_id = "agent-a"


def _message(**metadata: str) -> Message:
    return Message(
        channel="telegram", target="chat-1", body="remember tabs", metadata=metadata
    )


def test_bridge_asks_with_the_turn_channel_session_and_root_turn() -> None:
    policy = _RecordingPolicy(identity_constraints=["NEVER write memory records"])
    bridge = _Bridge(policy=policy, tools=_tools())
    gateway_ids = capture_identity_metadata(
        runtime_session_id="runtime-session", root_turn_id="run-1"
    )

    assert not bridge._turn_memory_write_allowed(
        message=_message(**gateway_ids), session_id="brain-session"
    )
    # Without the gateway's ids, the brain session id and no root turn.
    policy.update_identity_constraints([])
    assert bridge._turn_memory_write_allowed(
        message=_message(), session_id="brain-session"
    )

    assert [
        (c.context.channel, c.context.target, c.context.session_id, c.context.run_id)
        for c in policy.checks
    ] == [
        ("telegram", "memory-capture", "runtime-session", "run-1"),
        ("telegram", "memory-capture", "brain-session", ""),
    ]
    assert [d.decision for d in policy.decisions] == ["require_approval", "allow"]


@pytest.mark.parametrize(
    "metadata",
    [{"memory_consolidation_job": "true"}, {"diagnostic_scope": "runtime"}],
    ids=["excluded", "disabled"],
)
def test_bridge_asks_for_a_turn_without_capture(
    metadata: dict[str, str],
) -> None:
    # The turn's optional memory writes need the answer itself (#496); its
    # capture stays unauthorized (`_prepare_turn`).
    policy = _RecordingPolicy(identity_constraints=["NEVER write memory records"])
    bridge = _Bridge(policy=policy, tools=_tools())

    assert not bridge._turn_memory_write_allowed(
        message=_message(**metadata), session_id="brain-session"
    )
    policy.update_identity_constraints([])
    assert bridge._turn_memory_write_allowed(
        message=_message(**metadata), session_id="brain-session"
    )
    assert [d.decision for d in policy.decisions] == ["require_approval", "allow"]


def test_bridge_with_a_policy_and_no_tools_does_not_authorize() -> None:
    policy = _RecordingPolicy()

    assert not _Bridge(policy=policy, tools=None)._turn_memory_write_allowed(
        message=_message(), session_id="brain-session"
    )
    assert _Bridge(policy=None, tools=None)._turn_memory_write_allowed(
        message=_message(), session_id="brain-session"
    )
    assert policy.checks == []


def test_extraction_needs_the_turn_capture_authorization() -> None:
    def runner(**flags: bool) -> Any:
        return SimpleNamespace(**flags)

    message = "Please remember that I prefer tabs."
    assert _capture_user_message(runner(_capture_authorized_for_turn=True), message)
    assert not _capture_user_message(
        runner(_capture_authorized_for_turn=False), message
    )
    assert not _capture_user_message(runner(), message)
    assert not _capture_user_message(
        runner(_capture_authorized_for_turn=True, _capture_excluded_for_turn=True),
        message,
    )
    assert not _capture_user_message(
        runner(_capture_authorized_for_turn=True), 'tool time {"tz": "UTC"}'
    )


@pytest.mark.parametrize("authorized", [False, True])
def test_unauthorized_turn_writes_no_capture_bundle(authorized: bool) -> None:
    identity = build_capture_identity(
        runtime_session_id="session-1", root_turn_id="root-1"
    )
    memory_api = MagicMock()
    memory_api.apply_capture_bundle.return_value = {
        "capture_id": identity.capture_id,
        "disposition": "succeeded",
        "output_ids": ["cand-1"],
    }
    writer = MagicMock()
    runner = SimpleNamespace(
        memory_api=memory_api,
        terminal_capture_writer=writer,
        _capture_authorized_for_turn=authorized,
    )
    state = SimpleNamespace(
        agent_id="agent-1",
        memory_capture_report_root_turn_id="root-1",
        memory_capture_report={"items": [dict(CAPTURE_ITEM)]},
    )
    result = SimpleNamespace(working_state=state, memory_capture_bundle_result=None)

    BrainRunner._apply_pending_capture_bundle(runner, identity=identity, result=result)

    if authorized:
        memory_api.apply_capture_bundle.assert_called_once()
        writer.commit_capture_result_and_release_hold.assert_called_once()
        assert result.memory_capture_bundle_result["disposition"] == "succeeded"
        return
    memory_api.apply_capture_bundle.assert_not_called()
    writer.commit_capture_result_and_release_hold.assert_not_called()
    assert result.memory_capture_bundle_result == {
        "capture_id": identity.capture_id,
        "disposition": "pending",
        "error_code": "CAPTURE_RECOVERY_UNAUTHORIZED",
    }
