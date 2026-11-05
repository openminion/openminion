"""Run-status scenarios shared by the SQLite and Postgres tests (#352).

Each scenario appends events to a runtime `SessionStore` and compares the
readers in `openminion.modules.task.run` with a plain event-by-event
reduction over all of the session's events, which is their contract. The
reduction reads each event with the readers' own Python normalization
(`_to_run_event`, `_extract_event_error`, `_event_matches_thread`), so the
event facts the SQL selects by must agree with it on every payload.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from openminion.modules.storage.runtime.migrations import (
    DEFAULT_MIGRATIONS,
    Migration,
    MigrationRows,
)
from openminion.modules.storage.runtime.session_store import EventRecord, SessionStore
from openminion.modules.storage.runtime.session_store.backend import (
    RuntimeSessionStoreBackend,
)
from openminion.modules.storage.runtime.session_store.models import (
    RuntimeSessionStoreIntegrityError,
)
from openminion.modules.storage.runtime.session_store.rows import (
    EVENT_COLUMNS,
    metadata_json,
    row_to_event,
)
from openminion.modules.storage.runtime.session_store.run_facts import (
    backfill_event_run_facts,
    event_run_facts,
    key_digest,
)
from openminion.modules.task.run import (
    ATTACH_ROLE_WRITER,
    DELIVERY_STATE_ACKED,
    DELIVERY_STATE_DELIVERED,
    DELIVERY_STATE_NONE,
    DELIVERY_STATE_PERSISTED,
    RUN_STATE_CANCELLED,
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_QUEUED,
    RUN_STATE_RESPONDING,
    RUN_STATE_RUNNING,
    RUN_STATE_WAITING_TOOL,
    append_run_state_event,
    list_session_run_events,
    list_session_runs,
    resolve_invocation_terminal,
    resolve_thread_lifecycle,
)
from openminion.modules.task.run.status import (
    _event_matches_thread,
    _extract_event_error,
    _to_run_event,
)

EVENT_CLOCK = "openminion.modules.storage.runtime.session_store.lifecycle.utc_now_iso"
RUN_STATES = {
    RUN_STATE_QUEUED,
    RUN_STATE_RUNNING,
    RUN_STATE_WAITING_TOOL,
    RUN_STATE_RESPONDING,
    RUN_STATE_COMPLETED,
    RUN_STATE_FAILED,
    RUN_STATE_CANCELLED,
}
TERMINAL_STATES = {RUN_STATE_COMPLETED, RUN_STATE_FAILED, RUN_STATE_CANCELLED}
# Run ids that need JSON escaping, or that `LIKE` would treat as wildcards.
SPECIAL_RUN_IDS = ('q"uote', "back\\slash", "café", "per%cent", "under_score")
# Every character `str.strip()` removes; SQL `TRIM` without a set removes spaces only.
PADDING = "".join(chr(code) for code in range(0x3001) if chr(code).isspace())
# `metadata_json` stores NaN and Infinity as bare tokens and NUL as its JSON
# escape; Postgres and SQLite JSON functions do not read them as `json.loads`.
NUL = chr(0)
NAN, INF = float("nan"), float("inf")
EVENT_FACTS = (
    "openminion.modules.storage.runtime.session_store.lifecycle.event_run_facts"
)


@contextmanager
def stepping_clock() -> Iterator[list[str]]:
    """Patch the event clock: 1 s forward per append, 7 s back every 5th."""
    base = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)
    stamps: list[str] = []

    def tick(counter=itertools.count()) -> str:
        index = next(counter)
        offset = index - 8 * (index // 5)
        stamps.append((base + timedelta(seconds=offset, microseconds=1)).isoformat())
        return stamps[-1]

    with patch(EVENT_CLOCK, side_effect=tick):
        yield stamps


def append_runs(sessions: SessionStore, session_id: str) -> None:
    """Interleaved runs whose events cover every run-status fact."""

    def run(event_type: str, run_id: str, **payload: Any) -> None:
        sessions.append_event(
            session_id=session_id,
            event_type=event_type,
            payload={"run_id": run_id, **payload},
        )

    specials = itertools.cycle(SPECIAL_RUN_IDS)
    for index in range(6):
        long_run = "long"
        short = f"short-{index}"
        special = next(specials)
        run("run.queued", short, state="queued", step="turn.accepted")
        run("run.running", long_run, state="running", step=f"step-{index}")
        run("run.queued", special, state="queued", step="turn.accepted")
        run("run.waiting_tool", short, state="waiting_tool", step="", error=None)
        # A nested reference to another run, and a letter-case variant.
        run("run.running", f"nested-{index}", parent={"run_id": short})
        run("run.running", short.upper(), state="running")
        run("run.failed", special, state="failed", step="turn.failed", error=503)
        # The payload state decides over the event type when it is a run state.
        run("run.queued", short, state="completed", step="  ", error="  ")
        run("run.completed", f"suffix-{index}", state="bogus")
        run("run.failed", f"padded-{index}", state=" RUNNING ")
        run("run.cancel_requested", long_run)
        if index % 2:
            run("run.completed", long_run, state="completed", error="boom")
        sessions.append_event(
            session_id=session_id, event_type="tool.call", payload={"run_id": short}
        )


def append_payload_variants(sessions: SessionStore, session_id: str) -> None:
    """Run events with payload values the readers normalize in Python.

    `append_run_state_event(payload=...)` stores these as given: text padded
    with any whitespace `str.strip()` removes, JSON null, booleans, numbers, an
    empty object or array, and text that looks like a JSON token. A `RUN.` event
    type is not a run event: the `run.` prefix is matched case-sensitively.
    """

    def run(event_state: str, run_id: Any, /, **payload: Any) -> None:
        append_run_state_event(
            sessions,
            session_id=session_id,
            run_id="replaced",
            state=event_state,
            current_step="",
            payload={"run_id": run_id, **payload},
        )

    run("queued", "r", step="first", error="earlier error")
    run("running", f"{PADDING}r{PADDING}", step=PADDING, error=PADDING)
    run("failed", "r", state=f"{PADDING}RUNNING{PADDING}")
    # A zero-width space is not whitespace to `str.strip()`.
    run("queued", "\u200br")
    run("queued", 7, step=0, error=0)
    run("running", "7", step=False, error=False)
    run("completed", f"{PADDING}7", step=None, error=None)
    run("queued", None)
    run("queued", True, state=None)
    run("failed", {}, state=5)
    run("queued", [], error={})
    run("queued", "", error="no run")
    run("queued", PADDING, error="no run")
    # Text that looks like a JSON token stays text, and distinct runs stay apart.
    run("queued", "trace: NaN", step="[Infinity]", error="trace, -Infinity")
    run("running", "trace: 1e999", error="loss: NaN, retry")
    run("queued", "[NaN]")
    sessions.append_event(
        session_id=session_id,
        event_type="RUN.failed",
        payload={"run_id": "r", "state": "bogus"},
    )


def _all_events(sessions: SessionStore, session_id: str) -> list[EventRecord]:
    return sessions.list_events(session_id=session_id, limit=1_000_000)


def _run_events(sessions: SessionStore, session_id: str) -> list[EventRecord]:
    """The session's run events: a type that starts with lowercase `run.` (#396).

    Selected in Python from all of the session's events, as the thread
    projection and the invocation terminal select them, not by the store.
    """
    return [
        event
        for event in _all_events(sessions, session_id)
        if event.event_type.startswith("run.")
    ]


def expected_runs(
    sessions: SessionStore, session_id: str, *, limit: int, scan_limit: int
) -> list[dict[str, Any]]:
    run_events = _run_events(sessions, session_id)
    window = run_events[-max(limit, scan_limit) :]
    listed = {item.run_id for item in map(_to_run_event, window) if item is not None}
    runs: dict[str, dict[str, Any]] = {}
    for event in run_events:
        item = _to_run_event(event)
        if item is None or item.run_id not in listed:
            continue
        run = runs.setdefault(
            item.run_id,
            {
                "run_id": item.run_id,
                "started_at": event.created_at,
                "ended_at": "",
                "current_step": "",
                "error": "",
                "event_count": 0,
            },
        )
        run["event_count"] += 1
        run["state"] = item.state
        error = _extract_event_error(item.payload)
        if item.current_step:
            run["current_step"] = item.current_step
        if error:
            run["error"] = error
        if item.state in TERMINAL_STATES:
            run["ended_at"] = event.created_at
    ordered = sorted(
        runs.values(), key=lambda run: (run["started_at"], run["run_id"]), reverse=True
    )
    return ordered[: max(1, min(limit, 500))]


def check_run_readers(sessions: SessionStore, session_id: str) -> None:
    run_events = _run_events(sessions, session_id)
    run_count = len(run_events)
    for scan_limit in (1, 2, 5, 13, 40, run_count - 1, run_count, 2000):
        for limit in (1, 3, 20):
            expected = expected_runs(
                sessions, session_id, limit=limit, scan_limit=scan_limit
            )
            listed = [
                {key: getattr(run, key) for key in expected[0]}
                for run in list_session_runs(
                    sessions, session_id=session_id, limit=limit, scan_limit=scan_limit
                )
            ]
            assert listed == expected, (scan_limit, limit)
    items = [item for item in map(_to_run_event, run_events) if item is not None]
    for run_id in sorted({item.run_id for item in items}):
        own = [item.id for item in items if item.run_id == run_id]
        for limit in (1, 2, 1000):
            for requested in (run_id, f" {run_id}\t"):
                listed_ids = [
                    event.id
                    for event in list_session_run_events(
                        sessions, session_id=session_id, run_id=requested, limit=limit
                    )
                ]
                assert listed_ids == own[-limit:], (requested, limit)


def append_threads(sessions: SessionStore, session_id: str) -> None:
    """Interleaved threads; `quiet` starts first and is untouched for a while."""

    def event(event_type: str, thread_id: str | None, **payload: Any) -> None:
        if thread_id is not None:
            payload = {"conversation_id": "conv", "thread_id": thread_id, **payload}
        sessions.append_event(
            session_id=session_id, event_type=event_type, payload=payload
        )

    event("run.queued", "quiet", run_id="q1", state="queued", invocation_id="inv-q1")
    event("client.attach", "quiet", attach_id="writer-q", attach_role="writer")
    event("client.attach", "quiet", attach_id="observer-q", attach_role="observer")
    event("run.completed", "quiet", run_id="q1", state="completed")
    event("response.persisted", "quiet", run_id="q1")
    for index in range(12):
        run_id = f"b{index}"
        event("run.queued", "busy", run_id=run_id, invocation_id=f"inv-b{index}")
        event("run.queued", "busy", run_id=f"{run_id}-retry")
        event("client.attach", "busy", attach_id=f"attach-{index}", attach_role="")
        event("run.running", "busy", run_id=run_id, state="running")
        if index % 4 == 0:
            # No thread or conversation: it belongs to every thread of `conv`
            # only when the conversation is the session itself.
            event("session.note", None, note=index)
        event("run.completed", "busy", run_id=run_id, state="completed")
    event("run.cancel_requested", "busy", run_id="b11")
    event("client.detached", "busy", attach_id="attach-11")
    event("response.delivered", "quiet", run_id="q1")


def append_thread_payload_variants(sessions: SessionStore, session_id: str) -> None:
    """Thread events with payload values the readers normalize in Python."""

    def event(event_type: str, **payload: Any) -> None:
        sessions.append_event(
            session_id=session_id, event_type=event_type, payload=payload
        )

    padded = {"conversation_id": f"{PADDING}conv{PADDING}", "thread_id": "\nt\t"}
    event("run.queued", **padded, run_id="t1", invocation_id=f"{PADDING}inv-1")
    event("client.attach", **padded, attach_id=f"{PADDING}w1", attach_role=" WRITER\t")
    # JSON null reads as "None": an attach id, an invocation id, and a role
    # that is not the writer's.
    event("client.attach", conversation_id="conv", thread_id="t", attach_id=None)
    event(
        "client.attach",
        conversation_id="conv",
        thread_id="t",
        attach_id="o1",
        attach_role=None,
    )
    event(
        "run.queued",
        conversation_id="conv",
        thread_id="t",
        run_id="t2",
        invocation_id=None,
    )
    # Not a run event: the `run.` prefix is matched case-sensitively.
    event("RUN.running", conversation_id="conv", thread_id="t", run_id="t3")
    # False, 0 and null ids are empty: in every thread of the session's conversation.
    event(
        "run.running", conversation_id=False, thread_id=0, run_id="t4", state="running"
    )
    event("run.waiting_tool", conversation_id=None, thread_id=None, run_id="t5")
    # Ids that look like JSON tokens: their own conversation and thread.
    event(
        "run.queued",
        conversation_id="trace: NaN",
        thread_id="[NaN]",
        run_id="trace, Infinity",
        invocation_id="trace: 1e999",
    )
    event(
        "run.running", conversation_id="trace: 1e999", thread_id="[1e999]", run_id="x"
    )
    # A zero-width space is not whitespace: another thread.
    event("run.queued", conversation_id="conv", thread_id="\u200bt", run_id="zw")
    event("run.failed", **padded, run_id=f"{PADDING}t1{PADDING}", state="\tfailed\n")


def check_thread_payload_variants(sessions: SessionStore, session_id: str) -> None:
    for conversation_id, thread_id in (
        ("conv", "t"),
        (session_id, "t"),
        ("conv", "\u200bt"),
        ("", "t"),
        (f" conv{PADDING}", f"{PADDING}t"),
        ("trace: NaN", "[NaN]"),
        ("trace: 1e999", "[1e999]"),
    ):
        projection = resolve_thread_lifecycle(
            sessions,
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        expected = expected_thread(
            sessions,
            session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        assert {key: getattr(projection, key) for key in expected} == expected, (
            conversation_id,
            thread_id,
        )


def check_review_cases(
    sessions: SessionStore, new_session: Callable[[str], str]
) -> None:
    """Run id, state and thread ids are stripped; a blank error keeps the earlier one."""

    def append(session_id: str, state: str, **kwargs: Any) -> EventRecord:
        return append_run_state_event(
            sessions,
            session_id=session_id,
            run_id="r",
            state=state,
            current_step="",
            **kwargs,
        )

    session_id = new_session("padded-run-id")
    append(session_id, "queued")
    append(session_id, "running", payload={"run_id": " r "})
    runs = list_session_runs(sessions, session_id=session_id)
    assert [(run.run_id, run.event_count) for run in runs] == [("r", 2)]
    assert (
        len(list_session_run_events(sessions, session_id=session_id, run_id="r")) == 2
    )

    session_id = new_session("blank-error")
    append(session_id, "queued", payload={"error": "earlier error"})
    append(session_id, "running", payload={"error": "\n\t"})
    assert (
        list_session_runs(sessions, session_id=session_id)[0].error == "earlier error"
    )

    session_id = new_session("padded-state")
    append(session_id, "queued")
    append(session_id, "failed", payload={"state": "\trunning\n"})
    run = list_session_runs(sessions, session_id=session_id)[0]
    assert (run.state, run.ended_at) == (RUN_STATE_RUNNING, "")

    session_id = new_session("padded-thread")
    ids = {"conversation_id": "conv", "thread_id": "t"}
    append(session_id, "queued", **ids)
    failed = append(
        session_id,
        "failed",
        **ids,
        payload={"conversation_id": "\tconv\n", "thread_id": "\nt\t"},
    )
    projection = resolve_thread_lifecycle(sessions, session_id=session_id, **ids)
    assert (projection.latest_run_state, projection.latest_event_id) == (
        RUN_STATE_FAILED,
        failed.id,
    )


def check_token_text_ids(
    sessions: SessionStore, new_session: Callable[[str], str]
) -> None:
    """Ids that look like JSON tokens keep their exact text.

    Each id is a run, conversation and thread id, written with the ordinary
    `append_run_state_event` arguments; "trace: NaN" and "trace: 1e999" must
    stay two runs.
    """
    session_id = new_session("token-text-ids")
    ids = ("trace: NaN", "trace: 1e999", "trace, Infinity", "[NaN]")
    events = {
        value: append_run_state_event(
            sessions,
            session_id=session_id,
            run_id=value,
            state="queued",
            current_step="",
            conversation_id=value,
            thread_id=value,
        )
        for value in ids
    }
    runs = list_session_runs(sessions, session_id=session_id)
    assert sorted((run.run_id, run.event_count) for run in runs) == sorted(
        (value, 1) for value in ids
    )
    for value, event in events.items():
        listed = list_session_run_events(sessions, session_id=session_id, run_id=value)
        assert [item.id for item in listed] == [event.id], value
        projection = resolve_thread_lifecycle(
            sessions, session_id=session_id, conversation_id=value, thread_id=value
        )
        assert (projection.latest_run_id, projection.latest_event_id) == (
            value,
            event.id,
        ), value


def check_run_event_type_case(
    sessions: SessionStore, new_session: Callable[[str], str]
) -> None:
    """A run event's type starts with lowercase `run.`; its suffix is any case (#396).

    The events of the #396 report, plus a run whose only event has a lowercase
    `run.` prefix and an upper-case suffix. Each expected value is written out
    here, the same on every backend, and the stored event types stay as given.
    """
    session_id = new_session("run-event-type-case")
    ids = {"conversation_id": "conv", "thread_id": "t"}
    events = (
        ("run.queued", {"run_id": "r1", "state": "queued", "invocation_id": "inv1"}),
        ("run.running", {"run_id": "r1"}),
        ("RUN.failed", {"run_id": "r1", "state": "failed", "error": "boom"}),
        ("run.queued", {"run_id": "r2", "state": "queued"}),
        ("Run.completed", {"run_id": "r2"}),
        ("run.COMPLETED", {"run_id": "r3", "thread_id": "u"}),
    )
    written = [
        sessions.append_event(
            session_id=session_id,
            event_type=event_type,
            payload={**ids, **payload},
        )
        for event_type, payload in events
    ]
    assert [event.event_type for event in _all_events(sessions, session_id)] == [
        "run.queued",
        "run.running",
        "RUN.failed",
        "run.queued",
        "Run.completed",
        "run.COMPLETED",
    ]

    runs = list_session_runs(sessions, session_id=session_id)
    assert [
        (run.run_id, run.state, run.event_count, bool(run.ended_at), run.error)
        for run in runs
    ] == [
        ("r3", RUN_STATE_COMPLETED, 1, True, ""),
        ("r2", RUN_STATE_QUEUED, 1, False, ""),
        ("r1", RUN_STATE_RUNNING, 2, False, ""),
    ]
    r1, r2, r3 = (
        [
            (event.id, event.event_type, event.state)
            for event in list_session_run_events(
                sessions, session_id=session_id, run_id=run_id
            )
        ]
        for run_id in ("r1", "r2", "r3")
    )
    assert r1 == [
        (written[0].id, "run.queued", RUN_STATE_QUEUED),
        (written[1].id, "run.running", RUN_STATE_RUNNING),
    ]
    assert r2 == [(written[3].id, "run.queued", RUN_STATE_QUEUED)]
    assert r3 == [(written[5].id, "run.COMPLETED", RUN_STATE_COMPLETED)]

    # The thread projection and the invocation terminal already agree.
    projection = resolve_thread_lifecycle(sessions, session_id=session_id, **ids)
    assert (projection.latest_run_id, projection.latest_run_state) == (
        "r2",
        RUN_STATE_QUEUED,
    )
    terminal = resolve_invocation_terminal(
        sessions, session_id=session_id, trigger_event=written[2], **ids
    )
    assert terminal is None


def expected_thread(
    sessions: SessionStore, session_id: str, *, conversation_id: str, thread_id: str
) -> dict[str, Any]:
    conversation_value = str(conversation_id or "").strip()
    thread_value = str(thread_id or "").strip()
    events = [
        e
        for e in _all_events(sessions, session_id)
        if _event_matches_thread(
            e,
            conversation_id=conversation_value,
            thread_id=thread_value,
            session_id=session_id,
        )
    ]
    runs = [
        item
        for item in (
            _to_run_event(e) for e in events if e.event_type.startswith("run.")
        )
        if item is not None
    ]

    def text(event: EventRecord, key: str) -> str:
        return str(event.payload.get(key, "")).strip()

    starts = [
        e for e in events if e.event_type == "run.queued" and text(e, "invocation_id")
    ]
    writers = [
        text(e, "attach_id")
        for e in events
        if e.event_type == "client.attach"
        and text(e, "attach_id")
        and text(e, "attach_role").lower() in {"", ATTACH_ROLE_WRITER}
    ]
    types = {e.event_type for e in events}
    delivery = DELIVERY_STATE_NONE
    for event_type, state in (
        ("response.persisted", DELIVERY_STATE_PERSISTED),
        ("response.delivered", DELIVERY_STATE_DELIVERED),
        ("response.acked", DELIVERY_STATE_ACKED),
    ):
        if event_type in types:
            delivery = state
    return {
        "latest_run_id": runs[-1].run_id if runs else "",
        "latest_run_state": runs[-1].state if runs else "",
        "invocation_id": text(starts[-1], "invocation_id") if starts else "",
        "invocation_source_event_id": starts[-1].id if starts else 0,
        "latest_event_id": events[-1].id if events else 0,
        "updated_at": events[-1].created_at if events else "",
        "delivery_state": delivery,
        "writer_attach_id": writers[-1] if writers else "",
    }


def check_thread_projection(sessions: SessionStore, session_id: str) -> None:
    for conversation_id, thread_id in (
        ("conv", "quiet"),
        ("conv", "busy"),
        ("conv", "missing"),
        (session_id, "busy"),
        ("", "quiet"),
    ):
        projection = resolve_thread_lifecycle(
            sessions,
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        expected = expected_thread(
            sessions,
            session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        assert {key: getattr(projection, key) for key in expected} == expected, (
            conversation_id,
            thread_id,
        )


def facts_rows(sessions: SessionStore, session_id: str) -> list[dict[str, Any]]:
    """The session's `event_run_facts` rows, by event id."""
    return sessions._backend.query_dicts(
        "SELECT * FROM event_run_facts WHERE session_id = ? ORDER BY event_id",
        (session_id,),
    )


# Payload values the facts read: text the readers strip, a zero-width space
# they keep, JSON token text, every JSON type, and NaN, Infinity and NUL.
FACT_KEYS = (
    "run_id",
    "state",
    "step",
    "error",
    "conversation_id",
    "thread_id",
    "invocation_id",
    "attach_id",
    "attach_role",
)
FACT_VALUES: tuple[Any, ...] = (
    f"{PADDING}x{PADDING}",
    chr(0x200B) + "x",
    "trace: NaN",
    "a" + chr(92) + "u0000b",
    f"nul{NUL}id",
    NUL,
    PADDING,
    "",
    None,
    True,
    False,
    0,
    7,
    1e16,
    -0.0,
    "7",
    " RUNNING ",
    "Cancelled",
    " WRITER ",
    "observer",
    {},
    [],
    {"run_id": "r"},
    ["r", 1],
    NAN,
    INF,
    -INF,
)


def _dev_run_state(payload: dict[str, Any], event_type: str) -> str:
    """`_normalize_run_state` as `dev` wrote it, copied as the oracle."""
    candidate = str(payload.get("state") or "").strip().lower()
    if candidate in RUN_STATES:
        return candidate
    if event_type.startswith("run."):
        suffix = event_type.split(".", 1)[1].strip().lower()
        if suffix in RUN_STATES:
            return suffix
    return RUN_STATE_RUNNING


def check_event_facts(sessions: SessionStore, session_id: str) -> None:
    """Each event's facts are the values the readers read from its payload.

    The expected values apply `dev`'s expressions, copied here rather than
    imported, to the payload as the readers read it back (`list_events`), for
    every key and value above, in a run event and in a tool event.
    """
    for key in FACT_KEYS:
        for value in FACT_VALUES:
            for event_type in ("run.failed", "tool.call"):
                sessions.append_event(
                    session_id=session_id,
                    event_type=event_type,
                    payload={"run_id": "r", key: value},
                )
    events = {event.id: event for event in _all_events(sessions, session_id)}
    rows = facts_rows(sessions, session_id)
    assert [row["event_id"] for row in rows] == sorted(events)
    for row in rows:
        event = events[row["event_id"]]
        payload = event.payload
        run_id = str(payload.get("run_id", "")).strip()
        error = payload.get("error")

        def decoded(column: str) -> str:
            return json.loads(row[column]) if row[column] else ""

        actual = {
            "run_id": decoded("run_key"),
            "run_state": row["run_state"],
            "has_step": row["has_step"],
            "has_error": row["has_error"],
            "conversation_id": decoded("conversation_key"),
            "thread_id": decoded("thread_key"),
            "has_invocation": row["has_invocation"],
            "has_attach": row["has_attach"],
            "attach_role": decoded("attach_role"),
        }
        expected = {
            "run_id": run_id,
            "run_state": _dev_run_state(payload, event.event_type) if run_id else "",
            "has_step": int(bool(run_id and str(payload.get("step", "")).strip())),
            "has_error": int(bool(run_id and error is not None and str(error).strip())),
            "conversation_id": str(payload.get("conversation_id", "") or "").strip(),
            "thread_id": str(payload.get("thread_id", "") or "").strip(),
            "has_invocation": int(bool(str(payload.get("invocation_id", "")).strip())),
            "has_attach": int(bool(str(payload.get("attach_id", "")).strip())),
            "attach_role": str(payload.get("attach_role", "")).strip().lower(),
        }
        assert actual == expected, (row["event_id"], event.event_type, payload)
        assert row["session_id"] == session_id
        assert row["run_key_digest"] == key_digest(row["run_key"])
        for column in ("run_key", "conversation_key", "thread_key", "attach_role"):
            assert row[column].isascii() and NUL not in row[column], column


def append_nonstandard_values(sessions: SessionStore, session_id: str) -> None:
    """Run and tool events with NaN, Infinity and NUL values, as stored."""

    def event(event_type: str, **payload: Any) -> None:
        sessions.append_event(
            session_id=session_id,
            event_type=event_type,
            payload={"conversation_id": "conv", "thread_id": "t", **payload},
        )

    event("run.queued", run_id="r", error="earlier", step="s", invocation_id="inv")
    event("run.running", run_id="r", error=NAN)
    event("run.running", run_id="r", step=INF, latency=-INF)
    # Tool and attach events of the thread, which only the projection reads.
    event("tool.result", run_id="r", output=f"bin{NUL}ary", metrics=[NAN, INF])
    event("client.attach", attach_id=f"w{NUL}1", attach_role="writer")
    event("client.attach", attach_id="o1", attach_role=NAN)
    event("run.failed", run_id="r", error=f"bad{NUL}byte", state=NAN)
    event("run.queued", run_id=f"nul{NUL}id", invocation_id=INF)
    event("run.queued", run_id=NAN, state="queued")
    event("run.completed", run_id=-INF)
    nul_thread = {"conversation_id": f"c{NUL}", "thread_id": f"t{NUL}"}
    event("response.persisted", **nul_thread, run_id="x", note=NAN)
    event("run.queued", **nul_thread, run_id="x", error=-INF)


def check_nonstandard_values(
    sessions: SessionStore, session_id: str, *, nul_requests: bool = True
) -> None:
    """The readers agree with the event reduction on `append_nonstandard_values`.

    With `nul_requests`, the thread projection is also asked for the thread
    whose stored ids hold a NUL. Postgres takes no NUL in a text parameter, so
    on that backend the message read of `resolve_thread_lifecycle` rejects such
    a request on `dev` too; stored NUL values are read on both backends.
    """
    check_run_readers(sessions, session_id)
    requests = [("conv", "t"), (session_id, "t"), ("", "t")]
    if nul_requests:
        requests.append((f"c{NUL}", f"t{NUL}"))
    for conversation_id, thread_id in requests:
        projection = resolve_thread_lifecycle(
            sessions,
            session_id=session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        expected = expected_thread(
            sessions,
            session_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        assert {key: getattr(projection, key) for key in expected} == expected, (
            conversation_id,
            thread_id,
        )
    runs = {
        run.run_id: run for run in list_session_runs(sessions, session_id=session_id)
    }
    assert sorted(runs) == sorted(["r", f"nul{NUL}id", "nan", "-inf", "x"])
    r = runs["r"]
    assert (r.state, r.current_step, r.error, r.event_count) == (
        RUN_STATE_FAILED,
        "inf",
        f"bad{NUL}byte",
        4,
    )


def check_nonstandard_probe_cases(
    sessions: SessionStore, new_session: Callable[[str], str]
) -> None:
    """NaN, Infinity and NUL payload values after an earlier error.

    Both backends report what the readers' Python normalization reads from the
    later run event.
    """
    cases = {
        "error NaN": ({"error": NAN}, "s", "nan"),
        "step Infinity": ({"step": INF}, "inf", "earlier"),
        "other key -Infinity": ({"latency": -INF}, "s", "earlier"),
        "error with NUL": ({"error": f"bad{NUL}byte"}, "s", f"bad{NUL}byte"),
        "other key with NUL": ({"output": f"bin{NUL}ary"}, "s", "earlier"),
    }
    for name, (payload, step, error) in cases.items():
        session_id = new_session(name)
        common: dict[str, Any] = {
            "session_id": session_id,
            "run_id": "r",
            "current_step": "s",
            "conversation_id": "conv",
            "thread_id": "t",
        }
        append_run_state_event(
            sessions, **common, state="queued", payload={"error": "earlier"}
        )
        last = append_run_state_event(
            sessions, **common, state="running", payload=payload
        )
        runs = list_session_runs(sessions, session_id=session_id)
        assert [
            (run.run_id, run.state, run.current_step, run.error, run.event_count)
            for run in runs
        ] == [("r", RUN_STATE_RUNNING, step, error, 2)], name
        listed = list_session_run_events(sessions, session_id=session_id, run_id="r")
        assert len(listed) == 2, name
        projection = resolve_thread_lifecycle(
            sessions, session_id=session_id, conversation_id="conv", thread_id="t"
        )
        assert (
            projection.latest_run_id,
            projection.latest_run_state,
            projection.latest_event_id,
        ) == ("r", RUN_STATE_RUNNING, last.id), name


def recorded_backfill_migrations(batches: list[int]) -> tuple[Migration, ...]:
    """`DEFAULT_MIGRATIONS` whose migration 13 backfill records each batch it reads."""

    class Recorder:
        def __init__(self, rows: MigrationRows) -> None:
            self._rows = rows

        def query_dicts(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
            found = self._rows.query_dicts(sql, params)
            batches.append(len(found))
            return found

        def insert_many(self, table: str, rows: list[dict[str, Any]]) -> int:
            return self._rows.insert_many(table, rows)

    def backfill(rows: MigrationRows) -> None:
        backfill_event_run_facts(Recorder(rows))

    return tuple(
        dataclasses.replace(item, backfill=backfill) if item.version == 13 else item
        for item in DEFAULT_MIGRATIONS
    )


def legacy_event_rows(session_ids: Sequence[str], count: int) -> list[dict[str, Any]]:
    """`count` events as the store wrote them before runtime migration 13.

    Interleaved runs and threads over `session_ids`, with tool events, padded
    and JSON-token text, and NaN, Infinity and NUL values; `payload_json` is
    `metadata_json` of the payload, as `append_event` writes it.
    """
    base = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
    templates: tuple[tuple[str, dict[str, Any]], ...] = (
        ("run.queued", {"state": "queued", "step": "accepted", "invocation_id": "i"}),
        ("run.running", {"step": PADDING, "error": NAN}),
        ("tool.result", {"output": f"bin{NUL}ary", "metrics": [NAN, -INF]}),
        ("client.attach", {"attach_id": "w", "attach_role": " Writer "}),
        ("run.waiting_tool", {"step": INF, "error": f"bad{NUL}byte"}),
        ("RUN.failed", {"state": "bogus"}),
        ("run.completed", {"state": f"{PADDING}completed", "error": "trace: NaN"}),
        ("response.delivered", {}),
    )
    rows = []
    for index in range(count):
        event_type, extra = templates[index % len(templates)]
        turn = index // len(templates)
        payload = {
            "run_id": f"run-{turn % 7}" if index % 11 else f"nul{NUL}{turn % 3}",
            "conversation_id": "conv",
            "thread_id": f"t{turn % 3}",
            **extra,
        }
        stamp = base + timedelta(seconds=index - 8 * (index // 5))
        rows.append(
            {
                "session_id": session_ids[index % len(session_ids)],
                "event_type": event_type,
                "payload_json": metadata_json(payload),
                "created_at": stamp.isoformat(),
            }
        )
    return rows


def stored_events(sessions: SessionStore) -> list[dict[str, Any]]:
    """Every stored event row as the database returns it, by id."""
    return sessions._backend.query_dicts(
        f"SELECT {EVENT_COLUMNS} FROM events ORDER BY id"
    )


def check_upgraded_history(
    sessions: SessionStore,
    new_session: Callable[[str], str],
    *,
    session_ids: Sequence[str],
    stored: list[dict[str, Any]],
) -> None:
    """Check the events `stored` before runtime migration 13, once it has run.

    The rows are unchanged, each has the facts the write path gives the same
    payload, and the readers agree with the event reduction.
    """
    assert stored_events(sessions) == stored
    facts = sessions._backend.query_dicts(
        "SELECT * FROM event_run_facts ORDER BY event_id"
    )
    assert facts == [event_run_facts(row_to_event(row)) for row in stored]

    # The same payloads, appended through the store, get the same facts.
    fresh = new_session("written-after-upgrade")
    appended = [
        sessions.append_event(
            session_id=fresh,
            event_type=row["event_type"],
            payload=row_to_event(row).payload,
        )
        for row in stored[:40]
    ]
    rows_by_id = {row["id"]: row for row in stored_events(sessions)}
    assert [rows_by_id[event.id]["payload_json"] for event in appended] == [
        row["payload_json"] for row in stored[:40]
    ]

    def without_ids(row: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in row.items() if k not in ("event_id", "session_id")}

    assert [without_ids(row) for row in facts_rows(sessions, fresh)] == [
        without_ids(row) for row in facts[:40]
    ]

    for session_id in session_ids:
        check_run_readers(sessions, session_id)
        for conversation_id, thread_id in (
            ("conv", "t0"),
            ("conv", "t2"),
            (session_id, "t1"),
        ):
            projection = resolve_thread_lifecycle(
                sessions,
                session_id=session_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
            )
            expected = expected_thread(
                sessions,
                session_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
            )
            assert {key: getattr(projection, key) for key in expected} == expected


def check_fact_writes(
    sessions: SessionStore, new_session: Callable[[str], str]
) -> None:
    """Facts are written and removed with their events, in the same transaction."""
    session_id = new_session("fact-writes")

    def facts() -> list[tuple[int, str]]:
        return [
            (row["event_id"], row["run_key"])
            for row in facts_rows(sessions, session_id)
        ]

    # A canonical replay returns the stored event and keeps its facts; a replay
    # with another payload conflicts and writes nothing.
    first = sessions.append_event(
        session_id=session_id,
        event_type="run.queued",
        canonical_event_id="facts.v1:first",
        payload={"run_id": "first"},
    )
    replay = sessions.append_event(
        session_id=session_id,
        event_type="run.queued",
        canonical_event_id="facts.v1:first",
        payload={"run_id": "first"},
    )
    assert replay == first
    with pytest.raises(RuntimeSessionStoreIntegrityError):
        sessions.append_event(
            session_id=session_id,
            event_type="run.queued",
            canonical_event_id="facts.v1:first",
            payload={"run_id": "second"},
        )
    assert facts() == [(first.id, json.dumps("first"))]

    # The terminal outcome and its retention hold, then the capture result
    # that releases the hold: each is written once, with its facts.
    outcome_payload = {"capture_id": "cap", "capture_state": "pending", "run_id": "o"}
    outcome = sessions.commit_terminal_capture_intent(
        session_id=session_id,
        canonical_event_id="turn.outcome.v1:facts",
        capture_id="cap",
        payload=outcome_payload,
        payload_hash="h",
    )
    assert (
        sessions.commit_terminal_capture_intent(
            session_id=session_id,
            canonical_event_id="turn.outcome.v1:facts",
            capture_id="cap",
            payload=outcome_payload,
            payload_hash="h",
        )
        == outcome
    )
    with pytest.raises(RuntimeSessionStoreIntegrityError):
        sessions.commit_terminal_capture_intent(
            session_id=session_id,
            canonical_event_id="turn.outcome.v1:facts",
            capture_id="cap",
            payload={**outcome_payload, "run_id": "other"},
            payload_hash="h2",
        )
    result_payload = {"capture_id": "cap", "capture_state": "processed", "run_id": "c"}
    result = sessions.commit_capture_result_and_release_hold(
        session_id=session_id,
        canonical_event_id="memory.capture.result.v1:cap",
        capture_id="cap",
        payload=result_payload,
    )
    replayed = sessions.commit_capture_result_and_release_hold(
        session_id=session_id,
        canonical_event_id="memory.capture.result.v1:cap",
        capture_id="cap",
        payload=result_payload,
    )
    assert (result.replayed, replayed.replayed, replayed.event) == (
        False,
        True,
        result.event,
    )
    written = [
        (first.id, json.dumps("first")),
        (outcome.id, json.dumps("o")),
        (result.event.id, json.dumps("c")),
    ]
    assert facts() == written
    for row, event in zip(
        facts_rows(sessions, session_id), (first, outcome, result.event)
    ):
        assert row == event_run_facts(event)

    # A capture result without a hold fails after its insert: the event and
    # its facts roll back together.
    with pytest.raises(RuntimeSessionStoreIntegrityError):
        sessions.commit_capture_result_and_release_hold(
            session_id=session_id,
            canonical_event_id="memory.capture.result.v1:no-hold",
            capture_id="no-hold",
            payload={"capture_id": "no-hold", "run_id": "orphan"},
        )
    assert (
        sessions.get_event_by_canonical_id("memory.capture.result.v1:no-hold") is None
    )

    # A failure while writing the facts rolls the event back.
    high_water = sessions.event_high_water(session_id=session_id)
    with patch(EVENT_FACTS, side_effect=RuntimeError("facts failed")):
        with pytest.raises(RuntimeError, match="facts failed"):
            sessions.append_event(
                session_id=session_id, event_type="run.queued", payload={"run_id": "x"}
            )
    assert sessions.event_high_water(session_id=session_id) == high_water
    assert facts() == written
    assert [
        run.run_id for run in list_session_runs(sessions, session_id=session_id)
    ] == ["first"]

    # Deleting a session deletes its facts and no other session's.
    other = new_session("fact-writes-other")
    kept = sessions.append_event(
        session_id=other, event_type="run.queued", payload={"run_id": "first"}
    )
    assert sessions.delete_session(session_id)
    assert facts() == []
    assert [row["event_id"] for row in facts_rows(sessions, other)] == [kept.id]


def check_long_ids(sessions: SessionStore, new_session: Callable[[str], str]) -> None:
    """Long ids, also as JSON escapes, are written and read like short ones.

    The run key is indexed through a fixed-length digest, so its length adds no
    limit; Postgres B-tree entries are limited to about 2.7 kB. The run id also
    holds a NUL, which the store keeps escaped in the key.
    """
    session_id = new_session("long-ids")
    long_text = "".join(chr(0x4E00 + (index * 7919) % 20000) for index in range(4000))
    run_id = f"{long_text}{NUL}run"
    conversation_id, thread_id = f"{long_text}conv", f"{long_text}thread"
    appended = [
        append_run_state_event(
            sessions,
            session_id=session_id,
            run_id=run_id,
            state=state,
            current_step="",
            conversation_id=conversation_id,
            thread_id=thread_id,
        )
        for state in (RUN_STATE_QUEUED, RUN_STATE_FAILED)
    ]
    assert len(json.dumps(run_id)) > 20_000
    runs = list_session_runs(sessions, session_id=session_id)
    assert [(run.run_id, run.state, run.event_count) for run in runs] == [
        (run_id, RUN_STATE_FAILED, 2)
    ]
    listed = list_session_run_events(sessions, session_id=session_id, run_id=run_id)
    assert [event.id for event in listed] == [event.id for event in appended]
    projection = resolve_thread_lifecycle(
        sessions,
        session_id=session_id,
        conversation_id=conversation_id,
        thread_id=thread_id,
    )
    assert (projection.latest_run_id, projection.latest_event_id) == (
        run_id,
        appended[-1].id,
    )


def check_bounded_reads(sessions: SessionStore, session_id: str) -> None:
    """The rows the readers read do not grow with the history.

    Worst case: "long" runs from the first event to the last, interleaved with
    short runs on another thread, and thread-a's invocation starts at the
    first event. Rows are counted at the `RuntimeSessionStoreBackend` boundary.
    """
    reads: list[int] = []
    real_query = RuntimeSessionStoreBackend.query_dicts

    def counting_query(self: Any, sql: str, params: Any = None) -> Any:
        rows = real_query(self, sql, params)
        reads.append(len(rows))
        return rows

    def measured(read: Callable[[], Any]) -> tuple[Any, list[int]]:
        reads.clear()
        with patch.object(RuntimeSessionStoreBackend, "query_dicts", counting_query):
            result = read()
        return result, list(reads)

    def append(run_id: str, state: str, thread_id: str, **payload: Any) -> EventRecord:
        return append_run_state_event(
            sessions,
            session_id=session_id,
            run_id=run_id,
            state=state,
            current_step=state,
            conversation_id="conv",
            thread_id=thread_id,
            payload=payload or None,
        )

    turns = itertools.count()
    measurements = []
    start = append("long", RUN_STATE_QUEUED, "thread-a", invocation_id="inv-long")
    for added_turns in (60, 340):
        for _ in range(added_turns):
            turn = next(turns)
            last = append("long", RUN_STATE_RUNNING, "thread-a")
            append(f"short-{turn}", RUN_STATE_QUEUED, "thread-b", invocation_id="i")
            append(f"short-{turn}", RUN_STATE_COMPLETED, "thread-b")
        runs, runs_reads = measured(
            lambda: list_session_runs(
                sessions, session_id=session_id, limit=5, scan_limit=10
            )
        )
        long_events, long_reads = measured(
            lambda: list_session_run_events(
                sessions, session_id=session_id, run_id="long", limit=5
            )
        )
        projection, thread_reads = measured(
            lambda: resolve_thread_lifecycle(
                sessions,
                session_id=session_id,
                conversation_id="conv",
                thread_id="thread-a",
            )
        )
        history = sessions.event_high_water(session_id=session_id)
        measurements.append((runs_reads, long_reads, thread_reads))

        long_run = next(run for run in runs if run.run_id == "long")
        assert (long_run.event_count, long_run.started_at) == (
            1 + turn + 1,
            start.created_at,
        )
        assert [event.id for event in long_events][-1] == last.id
        assert (projection.invocation_id, projection.latest_event_id) == (
            "inv-long",
            last.id,
        )
        assert history - start.id + 1 in (181, 1201)
        # One aggregate (one row per run seen in the window of 10) and one
        # read by id (at most 4 rows per listed run).
        assert len(runs_reads) == 2
        assert runs_reads[0] <= 10 and runs_reads[1] <= 4 * 5
        assert long_reads == [5]
        # Recent messages, one aggregate row, and at most 4 rows by id.
        assert len(thread_reads) == 3 and thread_reads[1:] in ([1, 2], [1, 3])

    assert measurements[0] == measurements[1]
