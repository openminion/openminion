"""`Agent.run` and `Agent.run_stream` can answer a confirmation on its thread (#541).

The tests reuse the #448 harness in `test_turn_capture_authorization.py` and the
#524 handler recorder in `test_turn_denied_call_outcome.py`: a real `APIRuntime`
whose agent's `openai` provider is a scripted OpenAI-compatible server on
127.0.0.1. Once armed with arguments, the scripted model calls `memory_write`
with them. Without an approval callback that call then waits for the user's
confirmation, and the turn ends with "Policy confirmation required.". The
recorder lists each run of the tool's handler with the model's arguments.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from openminion import Agent
from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.services.runtime.ingress.types import TurnRequestError
from tests.runtime.test_turn_capture_authorization import (
    AGENT_ID,
    REPLY,
    _completion,
    _Provider,
    _runtime,
)
from tests.runtime.test_turn_denied_call_outcome import (
    ARGS,
    DENIED_REPLY,
    PENDING_REPLY,
    _callback,
    _record_handler,
)

# The message of the usage example in docs/runtime-surfaces.md; the model's
# call for it carries ARGS.
MESSAGE = "Remember that I write code in Iosevka 14."
OTHER_MESSAGE = "Remember that I prefer a light editor theme."
OTHER_ARGS = {
    "record_type": "fact",
    "key": "fact:editor_theme",
    "title": "Editor theme",
    "content": "The user prefers a light editor theme.",
}
HELPER_ID = "helper"


class _ArmedProvider(_Provider):
    """The #448 scripted provider; each `arm` makes the model call `memory_write`."""

    def __init__(self) -> None:
        super().__init__()
        self._armed: list[dict[str, Any]] = []
        self._calls = 0

    def arm(self, args: dict[str, Any]) -> None:
        with self._lock:
            self._armed.append(args)

    def _next_call(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._armed:
                return None
            self._calls += 1
            self.requests.append("tool_call")
            return {
                "id": f"call-memory-write-{self._calls}",
                "type": "function",
                "function": {
                    "name": "memory_write",
                    "arguments": json.dumps(self._armed.pop(0)),
                },
            }

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        names = [tool["function"]["name"] for tool in body.get("tools") or []]
        choice = body.get("tool_choice")
        forced = isinstance(choice, dict) and (
            (choice.get("function") or {}).get("name") == "submit_output"
        )
        call = self._next_call() if "memory_write" in names and not forced else None
        if call is None:
            return super().respond(body)
        return _completion(
            {"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"
        )


@pytest.fixture
def provider() -> Any:
    server = _ArmedProvider()
    try:
        yield server
    finally:
        server.close()


def _agent(runtime: Any, **agent_kwargs: Any) -> Agent:
    return Agent(runtime=runtime, forced_tools=["memory.write"], **agent_kwargs)


def _waiting(
    agent: Agent,
    provider: _ArmedProvider,
    args: dict[str, Any],
    message: str,
    **run_kwargs: Any,
) -> Any:
    """Run a turn whose `memory_write` call waits for the user's confirmation."""
    provider.arm(args)
    result = agent.run(message, **run_kwargs)
    assert result.run_state == "completed"
    assert PENDING_REPLY in result.text
    assert result.metadata["respond_kind"] == "policy_confirmation_prompt"
    assert result.metadata["pending_confirmation_command_id"]
    return result


def _assert_resumed(result: Any, thread_id: str) -> None:
    # The runtime's explicit-thread routing resumed the requested thread.
    assert result.run_state == "completed"
    assert result.thread_id == thread_id
    assert result.metadata["thread_id"] == thread_id
    assert result.metadata["thread_decision_action"] == "resume_thread"
    assert result.metadata["thread_decision_reason"] == "explicit_thread_requested"


def _model_args(approvals: list[Any]) -> list[Any]:
    # A replayed call carries its validated arguments, defaults included.
    return [(name, {key: args[key] for key in ARGS}) for name, args in approvals]


@pytest.mark.parametrize(
    "thread_id", [None, "", "  "], ids=["omitted", "empty", "blank"]
)
def test_reply_without_thread_id_keeps_the_default_routing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _ArmedProvider,
    thread_id: str | None,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    payloads: list[dict[str, Any]] = []
    run_turn = runtime.run_turn

    def recording_run_turn(*, payload: dict[str, Any], **kwargs: Any) -> Any:
        payloads.append(json.loads(json.dumps(payload)))
        return run_turn(payload=payload, **kwargs)

    monkeypatch.setattr(runtime, "run_turn", recording_run_turn)
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        first = _waiting(agent, provider, ARGS, MESSAGE)
        run_kwargs = {} if thread_id is None else {"thread_id": thread_id}
        reply = agent.run("yes", **run_kwargs)
    finally:
        runtime.close()

    # Both payloads are as before: the delivery flag alone, no thread.
    first_payload, reply_payload = payloads
    assert first_payload["inbound_metadata"] == {
        CALLER_HANDLES_DELIVERY_METADATA_KEY: "true"
    }
    assert reply_payload == {**first_payload, "message": "yes"}
    # The settled first thread is not resumed: the reply forks a new thread,
    # and the waiting call does not run.
    assert reply.run_state == "completed"
    assert reply.metadata["thread_decision_action"] == "fork_thread"
    assert reply.metadata["thread_decision_reason"] == "settled_without_resume"
    assert reply.metadata["thread_id"] != first.metadata["thread_id"]
    assert reply.text == f"{AGENT_ID}: {REPLY}"
    assert handled == []


def test_reply_on_its_thread_answers_the_confirmation_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _ArmedProvider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        provider.arm(ARGS)
        # The usage example in docs/runtime-surfaces.md.
        first = agent.run("Remember that I write code in Iosevka 14.")
        assert first.metadata.get("respond_kind") == "policy_confirmation_prompt"
        answer = agent.run("yes", thread_id=first.thread_id)
        ran = list(handled)
        # The confirmation is answered: a second "yes" on the thread is stale.
        again = agent.run("yes", thread_id=first.thread_id)
    finally:
        runtime.close()

    assert first.text.startswith(f"{AGENT_ID}: {PENDING_REPLY}")
    assert first.thread_id == first.metadata["thread_id"]
    _assert_resumed(answer, first.thread_id)
    assert answer.session_id == first.session_id
    assert answer.text == f"{AGENT_ID}: {REPLY}"
    # The waiting call ran once, with the model's arguments.
    assert ran == [ARGS]
    _assert_resumed(again, first.thread_id)
    assert again.text == f"{AGENT_ID}: {REPLY}"
    assert handled == [ARGS]


def test_denied_confirmation_does_not_run_on_a_later_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _ArmedProvider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        first = _waiting(agent, provider, ARGS, MESSAGE)
        denied = agent.run("no", thread_id=first.thread_id)
        # The confirmation is answered: a later "yes" on the thread is stale.
        later = agent.run("yes", thread_id=first.thread_id)
    finally:
        runtime.close()

    _assert_resumed(denied, first.thread_id)
    assert denied.text == f"{AGENT_ID}: {DENIED_REPLY}"
    _assert_resumed(later, first.thread_id)
    assert later.text == f"{AGENT_ID}: {REPLY}"
    assert handled == []


def test_pre_cancelled_reply_does_not_run_the_waiting_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _ArmedProvider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        first = _waiting(agent, provider, ARGS, MESSAGE)
        cancelled = Event()
        cancelled.set()
        # A request whose cancel_event is set before the call. Only whether the
        # waiting call runs is asserted, not how the cancelled turn ends. The
        # race between an approval and a cancellation (#527) is out of scope.
        with contextlib.suppress(RuntimeError):
            agent.run("yes", thread_id=first.thread_id, cancel_event=cancelled)
    finally:
        runtime.close()

    assert handled == []


def test_each_reply_runs_only_its_own_threads_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _ArmedProvider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        first = _waiting(agent, provider, ARGS, MESSAGE)
        second = _waiting(agent, provider, OTHER_ARGS, OTHER_MESSAGE)
        second_answer = agent.run("yes", thread_id=second.thread_id)
        ran = list(handled)
        first_answer = agent.run("yes", thread_id=first.thread_id)
    finally:
        runtime.close()

    # Two calls wait in one session, each on its own thread.
    assert second.session_id == first.session_id
    assert second.metadata["thread_decision_reason"] == "settled_without_resume"
    assert second.thread_id != first.thread_id
    _assert_resumed(second_answer, second.thread_id)
    assert ran == [OTHER_ARGS]
    _assert_resumed(first_answer, first.thread_id)
    assert handled == [OTHER_ARGS, ARGS]


def test_thread_of_another_session_runs_no_waiting_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _ArmedProvider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        first = _waiting(agent, provider, ARGS, MESSAGE)
        other = _waiting(
            agent, provider, OTHER_ARGS, OTHER_MESSAGE, session_id="other-session"
        )
        crossed = [
            # The first session's thread under another session id: one with
            # its own waiting call, and a new one.
            ("other-session", first.thread_id),
            ("new-session", first.thread_id),
            # The other session's thread under the first session's id.
            (agent.session_id, other.thread_id),
        ]
        replies = [
            agent.run("yes", session_id=session_id, thread_id=thread_id)
            for session_id, thread_id in crossed
        ]
        ran = list(handled)
        own = [
            agent.run("yes", thread_id=first.thread_id),
            agent.run("yes", session_id="other-session", thread_id=other.thread_id),
        ]
    finally:
        runtime.close()

    assert first.session_id == agent.session_id
    assert other.session_id == "other-session"
    # No crossed reply runs a waiting call. The routing resumes the requested
    # thread id under the caller's session, where no call waits on it, and
    # the model answers the "yes" as a new request.
    assert ran == []
    for (session_id, thread_id), reply in zip(crossed, replies, strict=True):
        assert reply.session_id == session_id
        _assert_resumed(reply, thread_id)
        assert reply.text == f"{AGENT_ID}: {REPLY}"
    # Each session's own reply still runs its own call, once.
    _assert_resumed(own[0], first.thread_id)
    _assert_resumed(own[1], other.thread_id)
    assert handled == [ARGS, OTHER_ARGS]


def test_thread_of_another_agent_runs_no_waiting_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _ArmedProvider
) -> None:
    runtime = _runtime(
        tmp_path, monkeypatch, provider, constraint=None, extra_agents=(HELPER_ID,)
    )
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        owner = _agent(runtime, agent_id=AGENT_ID)
        helper = _agent(runtime, agent_id=HELPER_ID)
        first = _waiting(owner, provider, ARGS, MESSAGE)
        # The owner's session belongs to the owner: the helper's turn is refused.
        with pytest.raises(TurnRequestError, match=f"belongs to agent '{AGENT_ID}'"):
            helper.run("yes", session_id=owner.session_id, thread_id=first.thread_id)
        # In the helper's own session no call waits on the owner's thread.
        borrowed = helper.run("yes", thread_id=first.thread_id)
        ran = list(handled)
        own = owner.run("yes", thread_id=first.thread_id)
    finally:
        runtime.close()

    assert ran == []
    assert borrowed.session_id == helper.session_id
    _assert_resumed(borrowed, first.thread_id)
    assert borrowed.text == f"{HELPER_ID}: {REPLY}"
    _assert_resumed(own, first.thread_id)
    assert handled == [ARGS]


@pytest.mark.parametrize("approve", [True, False], ids=["approved", "denied"])
@pytest.mark.parametrize("call", ["waiting", "new"])
def test_approval_callback_still_decides_on_an_explicit_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _ArmedProvider,
    call: str,
    approve: bool,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    approvals: list[Any] = []
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        if call == "waiting":
            # The call waiting on the thread: the callback decides, not "yes".
            first = _waiting(agent, provider, ARGS, MESSAGE)
            expected = ARGS
            message = "yes"
        else:
            # A new call made on the thread of an earlier, settled turn.
            first = agent.run("Hello.")
            provider.arm(OTHER_ARGS)
            expected = OTHER_ARGS
            message = OTHER_MESSAGE
        reply = agent.run(
            message,
            thread_id=first.thread_id,
            approval_callback=_callback(approvals, approve),
        )
    finally:
        runtime.close()

    _assert_resumed(reply, first.thread_id)
    assert _model_args(approvals) == [("memory.write", expected)]
    assert handled == ([expected] if approve else [])
    assert reply.text == f"{AGENT_ID}: {REPLY if approve else DENIED_REPLY}"


def test_run_stream_answers_on_its_thread_with_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _ArmedProvider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=None)
    events: list[tuple[int, Any]] = []
    stale_events: list[Any] = []
    try:
        handled = _record_handler(runtime, monkeypatch, fail=False)
        agent = _agent(runtime)
        first = _waiting(agent, provider, ARGS, MESSAGE)
        answer = agent.run_stream(
            "yes",
            thread_id=first.thread_id,
            # Each event with the handler runs so far.
            on_delta=lambda event: events.append((len(handled), event)),
        )
        ran = list(handled)
        again = agent.run_stream(
            "yes", thread_id=first.thread_id, on_delta=stale_events.append
        )
    finally:
        runtime.close()

    _assert_resumed(answer, first.thread_id)
    assert answer.text == f"{AGENT_ID}: {REPLY}"
    assert ran == [ARGS]
    # on_delta got the turn's progress while it ran: the tool's start before
    # the handler ran and its completion after it, and phase statuses on both
    # sides of it.
    tool_events = [
        (handler_runs, event["kind"], event["tool_name"])
        for handler_runs, event in events
        if isinstance(event, dict)
    ]
    assert tool_events == [
        (0, "tool_started", "memory.write"),
        (1, "tool_completed", "memory.write"),
    ]
    status_runs = {
        handler_runs
        for handler_runs, event in events
        if getattr(event, "status_key", None)
    }
    assert status_runs == {0, 1}
    # A second "yes" on the thread streams progress but runs nothing.
    _assert_resumed(again, first.thread_id)
    assert stale_events
    assert not [event for event in stale_events if isinstance(event, dict)]
    assert handled == [ARGS]
