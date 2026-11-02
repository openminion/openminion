"""Decision records and tool outcome candidates need the turn's memory.write answer (#496).

The tests reuse the #448 harness in `test_turn_capture_authorization.py`: a real
`APIRuntime` for one agent whose `openai` provider is a scripted
OpenAI-compatible server on 127.0.0.1, an identity profile with one hard
constraint unless a test says otherwise, and call-through recorders on the
security policy's `memory.write` decisions. They read the `decision` records in
`memory_records`, the `tool_outcome` candidates in `memory_candidates`, and the
events of `write_decision_memory`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from openminion.base.types import Message
from openminion.modules.brain.execution.memory import base as decision_memory
from tests.runtime.test_turn_capture_authorization import (
    AGENT_ID,
    MESSAGE,
    NEUTRAL_CONSTRAINT,
    REPLY,
    TOOL_ARGS,
    WRITE_CONSTRAINT,
    _consolidate,
    _Provider,
    _record,
    _runtime,
    _store_rows,
    _turn,
    _write_profile,
    provider as provider,
)

DENIED_REPLY = "Confirmation denied. The action was not run."


def _optional_rows(runtime: Any) -> dict[str, list[Any]]:
    store = runtime.resolve_memory_assembly(AGENT_ID).service._store
    connection = sqlite3.connect(str(store._store.db_path))
    try:
        decisions = connection.execute(
            "SELECT title FROM memory_records WHERE type = 'decision'"
            " ORDER BY created_at, id"
        ).fetchall()
        candidates = connection.execute(
            "SELECT title, proposed_scope, status, content_json"
            " FROM memory_candidates WHERE type = 'tool_outcome'"
            " ORDER BY created_at, candidate_id"
        ).fetchall()
    finally:
        connection.close()
    return {
        "decision": [title for (title,) in decisions],
        "tool_outcome": [
            (title, scope, status, json.loads(content)["outcome"])
            for title, scope, status, content in candidates
        ],
    }


def _record_decision_memory_events(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    emit = decision_memory._emit_memory_event

    def recording_emit(
        logger: Any, state: Any, event: str, payload: dict[str, Any], **kwargs: Any
    ) -> None:
        if event.startswith("brain.decision_memory."):
            events.append((event, payload.get("reason")))
        emit(logger, state, event, payload, **kwargs)

    monkeypatch.setattr(decision_memory, "_emit_memory_event", recording_emit)
    return events


def _two_turns(
    runtime: Any,
    provider: _Provider,
    approvals: list[tuple[Any, ...]],
    *,
    approve: bool = False,
) -> None:
    """A plain turn, then a turn whose model calls memory.write once."""

    def callback(*args: Any) -> bool:
        approvals.append(args)
        return approve

    _turn(runtime, provider, "plain", approval_callback=callback)
    provider.write_tool_call = True
    _turn(
        runtime,
        provider,
        "tool",
        forced_tools=["memory.write"],
        approval_callback=callback,
        reply=REPLY if approve else DENIED_REPLY,
    )


@pytest.mark.parametrize(
    ("case", "approve"),
    [("require-approval", False), ("require-approval", True), ("deny", False)],
    ids=["require-approval", "require-approval-model-call-approved", "deny"],
)
def test_turn_without_memory_write_writes_no_decision_record_or_tool_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
    case: str,
    approve: bool,
) -> None:
    constraint = WRITE_CONSTRAINT if case == "require-approval" else None
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=constraint)
    approvals: list[tuple[Any, ...]] = []
    try:
        if case == "deny":
            # memory.write as a power-user tool: the policy denies the internal
            # actor (`missing_tool_scope`), as in the #448 tests.
            monkeypatch.setattr(
                runtime.tools.get("memory.write"), "min_scope", "POWER_USER"
            )
        recorder = _record(runtime, monkeypatch)
        events = _record_decision_memory_events(monkeypatch)
        _two_turns(runtime, provider, approvals, approve=approve)
        rows = _optional_rows(runtime)
        tool_records = _store_rows(runtime)["tool_records"]
    finally:
        runtime.close()

    assert rows == {"decision": [], "tool_outcome": []}
    # Every decision of both turns is skipped; none is written.
    skipped = ("brain.decision_memory.skipped", "memory_write_not_authorized")
    assert len(events) >= 2
    assert set(events) == {skipped}
    # One memory.write question per turn, the turn's own. Nothing asks for
    # approval for these writes, and an approved model call does not change
    # the turn's answer.
    expected = (
        ("require_approval", "identity_hard_constraint_restricted")
        if case == "require-approval"
        else ("deny", "missing_tool_scope")
    )
    assert [(d[1], *d[4:]) for d in recorder.decisions if d[1] == "memory-capture"] == [
        ("memory-capture", *expected)
    ] * 2
    # The model's memory.write call keeps its approval: asked once, and the
    # record is written only when the user approves.
    assert [(name, args) for name, args, _id in approvals] == [
        ("memory.write", TOOL_ARGS)
    ]
    assert tool_records == int(approve)


@pytest.mark.parametrize(
    "case", ["neutral-constraint", "no-identity", "no-security-policy"]
)
def test_allowed_turn_writes_decision_records_as_before(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: _Provider,
    case: str,
) -> None:
    if case == "no-security-policy":
        monkeypatch.setenv("OPENMINION_DISABLE_SECURITY_POLICY", "true")
    constraint = NEUTRAL_CONSTRAINT if case == "neutral-constraint" else None
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=constraint)
    approvals: list[tuple[Any, ...]] = []
    try:
        recorder = _record(runtime, monkeypatch)
        events = _record_decision_memory_events(monkeypatch)
        _two_turns(runtime, provider, approvals)
        rows = _optional_rows(runtime)
    finally:
        runtime.close()

    # The user denied the model's call, so it did not run and stages no
    # tool_outcome candidate (#524).
    assert rows == {
        "decision": ["Decision: respond", "Decision: act"],
        "tool_outcome": [],
    }
    assert events == [("brain.decision_memory.completed", None)] * 2
    assert [(d[1], *d[4:]) for d in recorder.decisions] == (
        []
        if case == "no-security-policy"
        else [("memory-capture", "allow", "allowed")] * 2
    )
    assert [(name, args) for name, args, _id in approvals] == [
        ("memory.write", TOOL_ARGS)
    ]


def test_reused_runner_asks_the_memory_write_answer_on_every_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: _Provider
) -> None:
    runtime = _runtime(tmp_path, monkeypatch, provider, constraint=NEUTRAL_CONSTRAINT)
    try:
        service = runtime.resolve_agent_service(AGENT_ID)
        runner = service._get_runner()
        seen: list[tuple[Any, ...]] = []
        execute_turn = service._execute_turn

        def recording_execute_turn(**kwargs: Any) -> Any:
            turn_runner = kwargs["runner"]
            seen.append(
                (
                    turn_runner is runner,
                    getattr(turn_runner, "_memory_write_allowed_for_turn", None),
                    getattr(turn_runner, "_capture_authorized_for_turn", None),
                )
            )
            return execute_turn(**kwargs)

        monkeypatch.setattr(service, "_execute_turn", recording_execute_turn)
        recorder = _record(runtime, monkeypatch)
        recoveries: list[tuple[int, int, int]] = []
        new_decision_records: list[int] = []

        def constrain(constraint: str) -> None:
            _write_profile(tmp_path, constraint)
            service.identity_reload()

        def sdk_turn(session_id: str) -> None:
            _turn(runtime, provider, session_id)

        def consolidation_turn(run_id: str) -> None:
            recoveries.extend(_consolidate(runtime, monkeypatch, run_id).counts)

        def diagnostic_turn(check_id: str) -> None:
            # As `openminion agent-check` sends it: terminal capture is disabled.
            asyncio.run(
                service.run_turn(
                    Message(
                        channel="console",
                        target="api-user",
                        body=MESSAGE,
                        metadata={
                            "session_id": f"runtime:agent-check:{check_id}",
                            "request_id": check_id,
                            "turn_id": check_id,
                            "invocation_scope": "runtime",
                            "diagnostic_scope": "runtime",
                        },
                    )
                )
            )

        def counted(turn: Any, name: str) -> None:
            before = len(_optional_rows(runtime)["decision"])
            turn(name)
            new_decision_records.append(
                len(_optional_rows(runtime)["decision"]) - before
            )

        counted(sdk_turn, "allowed-1")
        constrain(WRITE_CONSTRAINT)
        counted(sdk_turn, "denied")
        counted(consolidation_turn, "consolidation-denied")
        counted(diagnostic_turn, "check-denied")
        constrain(NEUTRAL_CONSTRAINT)
        counted(sdk_turn, "allowed-2")
        counted(consolidation_turn, "consolidation-allowed")
        counted(diagnostic_turn, "check-allowed")
    finally:
        runtime.close()

    # (same runner, memory.write answer, capture authorization) per turn: the
    # excluded and disabled turns get the answer itself, and no capture.
    assert seen == [
        (True, True, True),
        (True, False, False),
        (True, False, False),
        (True, False, False),
        (True, True, True),
        (True, True, False),
        (True, True, False),
    ]
    assert new_decision_records == [1, 0, 0, 0, 1, 1, 1]
    # Every turn asks once, including the excluded and disabled turns.
    restricted = "require_approval"
    assert [d[4] for d in recorder.decisions if d[1] == "memory-capture"] == [
        "allow",
        restricted,
        restricted,
        restricted,
        "allow",
        "allow",
        "allow",
    ]
    # #448's pending capture: the denied turn's capture stays pending through
    # the denied consolidation run, and the allowed run commits it.
    assert recoveries == [(1, 0, 1), (1, 1, 0)]
