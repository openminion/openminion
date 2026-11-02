"""A tool call that did not run stages no `tool_outcome` candidate (#524).

The tests reuse the #448 harness in `test_turn_capture_authorization.py`: a real
`APIRuntime` for one agent whose `openai` provider is a scripted
OpenAI-compatible server on 127.0.0.1. The scripted model calls `memory_write`
once, with `ARGS`, and the agent's memory policy allows the write: an identity
profile with `NEVER reveal secrets`, or none, unless a test says otherwise. The
tests read `memory_candidates`, the `memory_records` row of `ARGS`, the
policy's `memory.write` decisions and the runs of the tool's handler.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from openminion import Agent
from openminion.modules.brain.loop.tools.contracts import canonical_tool_arguments
from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from tests.runtime.test_turn_capture_authorization import (
    AGENT_ID,
    MESSAGE,
    NEUTRAL_CONSTRAINT,
    REPLY,
    WRITE_CONSTRAINT,
    _events,
    _Provider,
    _record,
    _runtime,
)

ARGS = {
    "record_type": "fact",
    "key": "fact:editor_font",
    "title": "Editor font",
    "content": "The user writes code in Iosevka 14.",
}
# The call's argument text: its canonical signature and each value but the type.
ARGS_TEXT = [canonical_tool_arguments(ARGS), *list(ARGS.values())[1:]]
DENIED_REPLY = "Confirmation denied. The action was not run."
PENDING_REPLY = "Policy confirmation required."
FAILED_REPLY = "Tool execution failed"
FAILURE = "tool_outcome:memory.write:failure:EXEC_ERROR"


class _ArgsProvider(_Provider):
    """The #448 scripted provider; its one `memory_write` call carries ARGS."""

    def respond(self, body: dict[str, Any]) -> dict[str, Any]:
        reply = super().respond(body)
        for call in reply["choices"][0]["message"].get("tool_calls") or []:
            if call["function"]["name"] == "memory_write":
                call["function"]["arguments"] = json.dumps(ARGS)
        return reply


@pytest.fixture
def provider() -> Any:
    server = _ArgsProvider()
    server.write_tool_call = True
    try:
        yield server
    finally:
        server.close()


def _rows(runtime: Any) -> dict[str, list[Any]]:
    store = runtime.resolve_memory_assembly(AGENT_ID).service._store
    connection = sqlite3.connect(str(store._store.db_path))
    try:
        candidates = connection.execute(
            "SELECT type, title, content_json, meta_json FROM memory_candidates"
            " ORDER BY created_at, candidate_id"
        ).fetchall()
        records = connection.execute(
            "SELECT title FROM memory_records WHERE key = ?", (ARGS["key"],)
        ).fetchall()
    finally:
        connection.close()
    return {
        "candidates": candidates,
        "tool_outcome": [
            (title, json.loads(content), json.loads(meta))
            for kind, title, content, meta in candidates
            if kind == "tool_outcome"
        ],
        "records": [title for (title,) in records],
    }


def _assert_no_argument_text(candidates: list[Any]) -> None:
    # No candidate of any type holds the call's arguments in its title,
    # content or metadata.
    for _kind, title, content, meta in candidates:
        for stored in (title, content, meta):
            assert not [text for text in ARGS_TEXT if text in stored]


def _run(runtime: Any, text: str, session_id: str, **run_kwargs: Any) -> Any:
    with Agent(runtime=runtime, forced_tools=["memory.write"]) as agent:
        result = agent.run(text, session_id=session_id, **run_kwargs)
    assert result.run_state == "completed"
    return result


def _record_handler(
    runtime: Any, monkeypatch: pytest.MonkeyPatch, *, fail: bool
) -> list[dict[str, Any]]:
    """Record each run of memory.write's handler; with `fail`, it then raises."""
    handled: list[dict[str, Any]] = []
    spec = runtime.tools.get("memory.write")
    handler = spec.handler

    def recording_handler(args: dict[str, Any], ctx: Any) -> Any:
        handled.append({key: args[key] for key in ARGS})
        if fail:
            raise RuntimeError("memory backend unavailable")
        return handler(args, ctx)

    monkeypatch.setattr(spec, "handler", recording_handler)
    return handled


def _callback(approvals: list[Any], approve: bool) -> Any:
    def callback(tool_name: str, args: dict[str, Any], _approval_id: str) -> bool:
        approvals.append((tool_name, args))
        return approve

    return callback


@pytest.mark.parametrize("call", ["denied", "pending"])
@pytest.mark.parametrize("case", ["neutral-constraint", "no-identity"])
def test_call_that_did_not_run_stages_no_tool_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _ArgsProvider,
    case: str,
    call: str,
) -> None:
    constraint = NEUTRAL_CONSTRAINT if case == "neutral-constraint" else None
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=constraint)
    approvals: list[Any] = []
    try:
        recorder = _record(runtime, monkeypatch)
        handled = _record_handler(runtime, monkeypatch, fail=False)
        run_kwargs = (
            {"approval_callback": _callback(approvals, False)}
            if call == "denied"
            else {}
        )
        result = _run(runtime, MESSAGE, "call", **run_kwargs)
        rows = _rows(runtime)
        prompts = _events(runtime, "policy_confirmation_prompt")
    finally:
        runtime.close()

    reply = DENIED_REPLY if call == "denied" else PENDING_REPLY
    assert result.text.startswith(f"{AGENT_ID}: {reply}")
    # The memory policy allows the write: the turn's own memory.write answer.
    assert [d[1:2] + d[4:] for d in recorder.decisions] == [
        ("memory-capture", "allow", "allowed")
    ]
    assert approvals == ([("memory.write", ARGS)] if call == "denied" else [])
    # The call did not run: no record and no tool_outcome candidate, and no
    # candidate holds its arguments. Its approval prompt is still recorded.
    assert handled == []
    assert rows["records"] == []
    assert rows["tool_outcome"] == []
    _assert_no_argument_text(rows["candidates"])
    assert len(prompts) == 1


@pytest.mark.parametrize("case", ["succeeded", "failed", "failed-without-memory-write"])
def test_executed_call_keeps_its_tool_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _ArgsProvider,
    case: str,
) -> None:
    constraint = (
        WRITE_CONSTRAINT
        if case == "failed-without-memory-write"
        else NEUTRAL_CONSTRAINT
    )
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=constraint)
    approvals: list[Any] = []
    try:
        handled = _record_handler(runtime, monkeypatch, fail=case != "succeeded")
        result = _run(
            runtime,
            MESSAGE,
            "call",
            approval_callback=_callback(approvals, True),
        )
        rows = _rows(runtime)
    finally:
        runtime.close()

    # The user approves the call and it runs once.
    assert approvals == [("memory.write", ARGS)]
    assert handled == [ARGS]
    if case == "succeeded":
        # memory.write is not in the success allowlist: no candidate, as before.
        assert result.text == f"{AGENT_ID}: {REPLY}"
        assert rows["records"] == [ARGS["title"]]
        assert rows["tool_outcome"] == []
        return
    assert result.text == f"{AGENT_ID}: {FAILED_REPLY}"
    assert rows["records"] == []
    if case == "failed-without-memory-write":
        # The turn's memory.write answer (#496) still suppresses it.
        assert rows["tool_outcome"] == []
        return
    # A call that ran and failed keeps its candidate with its arguments.
    [(title, content, meta)] = rows["tool_outcome"]
    assert title == FAILURE
    assert content["outcome"] == "failure"
    assert content["args_signature"] == canonical_tool_arguments(ARGS)
    assert meta["source_args_signature"] == canonical_tool_arguments(ARGS)


@pytest.mark.parametrize("executed", ["succeeded", "failed"])
def test_pending_call_approved_later_stages_once_after_it_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _ArgsProvider,
    executed: str,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=NEUTRAL_CONSTRAINT)
    try:
        handled = _record_handler(runtime, monkeypatch, fail=executed == "failed")
        # No approval callback: the call waits for the user's confirmation.
        pending = _run(runtime, MESSAGE, "call")
        pending_rows = _rows(runtime)
        # The user's reply on the same thread. Agent.run would start a new
        # thread for a settled turn, so the reply names the first turn's thread.
        approved = runtime.run_turn(
            payload={
                "message": "yes",
                "session_id": "call",
                "deliver": False,
                "forced_tools": ["memory.write"],
                "inbound_metadata": {
                    CALLER_HANDLES_DELIVERY_METADATA_KEY: "true",
                    "thread_id": pending.metadata["thread_id"],
                },
            }
        )
        rows = _rows(runtime)
    finally:
        runtime.close()

    assert pending.text.startswith(f"{AGENT_ID}: {PENDING_REPLY}")
    assert approved["metadata"]["thread_decision_action"] == "resume_thread"
    # Nothing is staged while the call waits.
    assert pending_rows["tool_outcome"] == []
    _assert_no_argument_text(pending_rows["candidates"])
    # Approved, the call runs once, and only its own outcome is staged.
    assert handled == [ARGS]
    if executed == "succeeded":
        assert approved["body"] == f"{AGENT_ID}: {REPLY}"
        assert rows["records"] == [ARGS["title"]]
        assert rows["tool_outcome"] == []
        _assert_no_argument_text(rows["candidates"])
        return
    assert rows["records"] == []
    [(title, content, meta)] = rows["tool_outcome"]
    assert title == FAILURE
    # The replayed call carries its validated arguments, defaults included.
    signature = json.loads(content["args_signature"])
    assert {key: signature[key] for key in ARGS} == ARGS
    assert meta["source_args_signature"] == content["args_signature"]
