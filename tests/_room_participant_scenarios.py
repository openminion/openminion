"""Room participant scenarios shared by the SQLite and Postgres tests.

Joins run with the participant clock patched to one timestamp and the row ids
patched so that each id sorts before the id of the earlier join, so neither
`joined_at` nor the id gives the join order. A join, selection or leave reads
and writes the active agent under the session-row lock in its own transaction;
the two-connection scenarios hold one of them there and check that the other
connection's call waits for its commit.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from openminion.modules.storage.runtime.session_store import (
    RoomParticipant,
    SessionStore,
    build_room_session_key,
)

PARTICIPANT_CLOCK = (
    "openminion.modules.storage.runtime.session_store.participants.utc_now_iso"
)
PARTICIPANT_UUID = "openminion.modules.storage.runtime.session_store.participants.uuid4"
EARLIER_AT = "2026-10-22T00:59:00+00:00"
TIED_AT = "2026-10-22T01:00:00+00:00"
LATER_AT = "2026-10-22T01:01:00+00:00"
# The order `list_participants` read before runtime migration 14.
PRE_UPGRADE_ORDER_SQL = (
    "SELECT participant_id FROM room_participants "
    "WHERE session_id = ? AND left_at IS NULL ORDER BY joined_at ASC, id ASC"
)
# An independent numbering to check the migration's ROW_NUMBER() against: each
# row counts the rows of its session at or before it in (joined_at, id) order.
COUNTED_JOIN_SEQ_SQL = """
SELECT participant.id AS id, (
    SELECT COUNT(*)
    FROM room_participants AS earlier
    WHERE earlier.session_id = participant.session_id
      AND (earlier.joined_at, earlier.id) <= (participant.joined_at, participant.id)
) AS join_seq
FROM room_participants AS participant
"""


def row_ids(chars: str) -> list[SimpleNamespace]:
    """`uuid4()` results `"f" * 32`, `"c" * 32`, ... for the given characters."""
    return [SimpleNamespace(hex=char * 32) for char in chars]


def participant_ids(sessions: SessionStore, session_id: str) -> list[str]:
    return [item.participant_id for item in sessions.list_participants(session_id)]


def join_seq_by_participant(sessions: SessionStore, session_id: str) -> dict[str, int]:
    rows = sessions._backend.query_dicts(
        "SELECT participant_id, join_seq FROM room_participants WHERE session_id = ?",
        (session_id,),
    )
    return {str(row["participant_id"]): int(row["join_seq"]) for row in rows}


def join_agents(
    sessions: SessionStore,
    session_id: str,
    agents: Sequence[str],
    *,
    ids: str,
    at: str = TIED_AT,
) -> list[RoomParticipant]:
    """Add each agent in turn; `ids` gives the row id each add generates."""
    with (
        patch(PARTICIPANT_CLOCK, return_value=at),
        patch(PARTICIPANT_UUID, side_effect=row_ids(ids)),
    ):
        return [
            sessions.add_participant(
                session_id=session_id, participant_type="agent", participant_id=agent
            )
            for agent in agents
        ]


def remove_agent(
    sessions: SessionStore, session_id: str, agent: str, *, at: str = TIED_AT
) -> None:
    with patch(PARTICIPANT_CLOCK, return_value=at):
        assert sessions.remove_participant(
            session_id=session_id, participant_type="agent", participant_id=agent
        )


def check_tied_join_order(sessions: SessionStore) -> None:
    """The issue's probe: `first`, `second`, `third` join on one tick."""
    room = sessions.create_room(channel="cli", target="review")
    joined = join_agents(sessions, room.id, ("first", "second", "third"), ids="fc0")

    assert [(item.id, item.joined_at) for item in joined] == [
        ("f" * 32, TIED_AT),
        ("c" * 32, TIED_AT),
        ("0" * 32, TIED_AT),
    ]
    assert participant_ids(sessions, room.id) == ["first", "second", "third"]
    assert sessions.get_active_agent(room.id) == "first"
    assert join_seq_by_participant(sessions, room.id) == {
        "first": 1,
        "second": 2,
        "third": 3,
    }

    remove_agent(sessions, room.id, "first")
    assert sessions.get_active_agent(room.id) == "second"
    assert participant_ids(sessions, room.id) == ["second", "third"]


def check_join_place_on_upsert_and_rejoin(sessions: SessionStore) -> None:
    room = sessions.create_room(channel="cli", target="rejoin")
    join_agents(sessions, room.id, ("first", "second", "third"), ids="fc0")

    # Added again while active, later: the same row keeps its place and
    # joined_at, and takes the new channel, role and display name.
    with (
        patch(PARTICIPANT_CLOCK, return_value=LATER_AT),
        patch(PARTICIPANT_UUID, side_effect=row_ids("9")),
    ):
        again = sessions.add_participant(
            session_id=room.id,
            participant_type="agent",
            participant_id="first",
            channel="cli",
            role="owner",
            display_name="First again",
        )
    assert (again.id, again.joined_at, again.channel, again.role) == (
        "f" * 32,
        TIED_AT,
        "cli",
        "owner",
    )
    assert again.display_name == "First again"
    assert participant_ids(sessions, room.id) == ["first", "second", "third"]
    assert join_seq_by_participant(sessions, room.id) == {
        "first": 1,
        "second": 2,
        "third": 3,
    }

    # Leaves and rejoins on the same tick: a new place after every earlier
    # join, on the same row.
    remove_agent(sessions, room.id, "first")
    rejoined = join_agents(sessions, room.id, ("first",), ids="8")[0]
    assert (rejoined.id, rejoined.joined_at) == ("f" * 32, TIED_AT)
    assert participant_ids(sessions, room.id) == ["second", "third", "first"]
    assert join_seq_by_participant(sessions, room.id) == {
        "first": 4,
        "second": 2,
        "third": 3,
    }

    # Leaves and rejoins later: the rejoin's joined_at too.
    remove_agent(sessions, room.id, "second", at=LATER_AT)
    rejoined = join_agents(sessions, room.id, ("second",), ids="7", at=LATER_AT)[0]
    assert (rejoined.id, rejoined.joined_at) == ("c" * 32, LATER_AT)
    assert participant_ids(sessions, room.id) == ["third", "first", "second"]
    assert join_seq_by_participant(sessions, room.id) == {
        "first": 4,
        "second": 5,
        "third": 3,
    }


def check_explicit_active_agent(sessions: SessionStore) -> None:
    room = sessions.create_room(channel="cli", target="explicit")
    join_agents(sessions, room.id, ("first", "second", "third"), ids="fc8")
    sessions.set_active_agent(session_id=room.id, agent_id="third")

    # A later join and an active participant added again keep the selection,
    # and so does a participant that is not active leaving.
    join_agents(sessions, room.id, ("fourth", "first"), ids="05")
    assert sessions.get_active_agent(room.id) == "third"
    remove_agent(sessions, room.id, "second")
    assert sessions.get_active_agent(room.id) == "third"

    # The active agent leaving selects the earliest remaining join.
    remove_agent(sessions, room.id, "third")
    assert participant_ids(sessions, room.id) == ["first", "fourth"]
    assert sessions.get_active_agent(room.id) == "first"

    # An explicit selection of a later join still wins; an agent that left
    # cannot be selected.
    sessions.set_active_agent(session_id=room.id, agent_id="fourth")
    assert sessions.get_active_agent(room.id) == "fourth"
    with pytest.raises(ValueError, match="not an active participant"):
        sessions.set_active_agent(session_id=room.id, agent_id="second")
    assert sessions.get_active_agent(room.id) == "fourth"


def check_legacy_owner_join_order(sessions: SessionStore) -> None:
    """A legacy session's owner record and the first invited agent, on one tick."""
    session = sessions.resolve_session(
        agent_id="legacy-agent", channel="console", target="legacy-tie"
    )
    join_agents(sessions, session.id, ("guest-agent",), ids="f0")

    assert participant_ids(sessions, session.id) == ["legacy-agent", "guest-agent"]
    assert join_seq_by_participant(sessions, session.id) == {
        "legacy-agent": 1,
        "guest-agent": 2,
    }


def check_joins_write_no_session_value(sessions: SessionStore) -> None:
    """Allocating a join place writes no value of the session row."""
    room = sessions.create_room(channel="cli", target="session-row")
    before = sessions.get_session(room.id)
    with patch(PARTICIPANT_CLOCK, return_value=LATER_AT):
        sessions.add_participant(
            session_id=room.id, participant_type="human", participant_id="alice"
        )
    assert sessions.get_session(room.id) == before

    # The first agent becomes the active agent, as before; later joins, and
    # the active agent added again, leave the session row as it is.
    join_agents(sessions, room.id, ("first",), ids="f")
    selected = sessions.get_session(room.id)
    assert selected is not None and selected.active_agent_id == "first"
    join_agents(sessions, room.id, ("second", "first"), ids="c0", at=LATER_AT)
    assert sessions.get_session(room.id) == selected


def check_concurrent_joins(
    sessions: SessionStore, peers: Sequence[SessionStore], *, joins: int = 5
) -> None:
    """Peers on their own connections join agents to one room on one tick."""
    room = sessions.create_room(channel="cli", target="crowd")
    start = threading.Barrier(len(peers))

    def join(index: int) -> list[RoomParticipant]:
        start.wait()
        return [
            peers[index].add_participant(
                session_id=room.id,
                participant_type="agent",
                participant_id=f"peer-{index}-{step}",
            )
            for step in range(joins)
        ]

    with patch(PARTICIPANT_CLOCK, return_value=TIED_AT):
        with ThreadPoolExecutor(max_workers=len(peers)) as pool:
            per_peer = list(pool.map(join, range(len(peers))))

    # Each peer's joins list in the order that peer made them.
    listed = participant_ids(sessions, room.id)
    for joined in per_peer:
        own = [item.participant_id for item in joined]
        assert [name for name in listed if name in own] == own
    # Every join has its own place.
    join_seq = join_seq_by_participant(sessions, room.id)
    assert sorted(join_seq.values()) == list(range(1, len(peers) * joins + 1))
    assert listed == sorted(join_seq, key=join_seq.get)
    assert sessions.get_active_agent(room.id) in join_seq


def check_leave_and_rejoin_wait_for_each_other(
    sessions: SessionStore, leaver: SessionStore, joiner: SessionStore
) -> None:
    """The active agent rejoins on one connection while it leaves on another.

    The rejoin starts after the leave has marked the participant as left and
    before the leave selects the next active agent, in the leave's open
    transaction. Both lock the session row first, so the rejoin waits for the
    leave to commit. Taking the participant row first in the leave and the
    session row first in the join deadlocked on Postgres.
    """
    room = sessions.create_room(channel="cli", target="leave-rejoin")
    join_agents(sessions, room.id, ("first", "second"), ids="fc")
    marked_left = threading.Event()
    rejoining = threading.Event()
    owner = leaver._participants
    get_active_agent = owner.get_active_agent

    def get_active_agent_once_rejoin_started(session_id: str) -> str | None:
        if not marked_left.is_set():
            marked_left.set()
            assert rejoining.wait(timeout=10)
            # Let the rejoin reach its first lock before the leave goes on.
            time.sleep(0.5)
        return get_active_agent(session_id)

    def leave() -> None:
        with patch.object(
            owner, "get_active_agent", get_active_agent_once_rejoin_started
        ):
            assert leaver.remove_participant(
                session_id=room.id, participant_type="agent", participant_id="first"
            )

    def rejoin() -> RoomParticipant:
        assert marked_left.wait(timeout=10)
        rejoining.set()
        return joiner.add_participant(
            session_id=room.id, participant_type="agent", participant_id="first"
        )

    with patch(PARTICIPANT_CLOCK, return_value=TIED_AT):
        with ThreadPoolExecutor(max_workers=2) as pool:
            left = pool.submit(leave)
            rejoined = pool.submit(rejoin)
            left.result(timeout=30)
            assert rejoined.result(timeout=30).id == "f" * 32

    assert participant_ids(sessions, room.id) == ["second", "first"]
    assert join_seq_by_participant(sessions, room.id) == {"first": 3, "second": 2}
    assert sessions.get_active_agent(room.id) == "second"


# How long a held call keeps another connection's call waiting before the test
# lets it go: below the 5 s SQLite busy timeout of the runtime storages.
HOLD_SECONDS = 1.0
HOLD_TIMEOUT = 10


class ActivationHold:
    """A join held right after the read that decides its activation."""

    def __init__(self) -> None:
        self.read = threading.Event()
        self.release = threading.Event()
        self.seen: str | None = None


@contextmanager
def held_activation_read(peer: SessionStore) -> Iterator[ActivationHold]:
    """Hold the next join on `peer` between its active-agent read and write.

    A join decides whether its agent becomes the active agent from the first
    session read after it writes the participant row. The hold records what
    that read found in `seen` and keeps the join there until `release` is set.
    Before #393 that read and the write that follows ran after the
    participant transaction had committed; they now run in it, under the
    session-row lock.
    """
    owner = peer._participants
    backend = owner._backend
    hold = ActivationHold()
    wrote_row = threading.Event()
    execute_count = backend.execute_count
    get_session = owner._get_session

    def execute_count_noting_the_row(sql: str, params: Any = None) -> int:
        count = execute_count(sql, params)
        if "INSERT INTO room_participants" in sql:
            wrote_row.set()
        return count

    def get_session_held_after_the_row(session_id: str) -> Any:
        record = get_session(session_id)
        if wrote_row.is_set() and not hold.read.is_set():
            hold.seen = None if record is None else record.active_agent_id
            hold.read.set()
            assert hold.release.wait(timeout=HOLD_TIMEOUT)
        return record

    with (
        patch.object(backend, "execute_count", execute_count_noting_the_row),
        patch.object(owner, "_get_session", get_session_held_after_the_row),
    ):
        try:
            yield hold
        finally:
            hold.release.set()


def finished_while_held(future: Future[Any]) -> bool:
    """Whether `future` finishes within `HOLD_SECONDS` while a call is held."""
    done, _ = wait([future], timeout=HOLD_SECONDS)
    return bool(done)


def new_room(sessions: SessionStore, target: str) -> str:
    """A room with its human owner, as `openminion room create` makes it."""
    room = sessions.create_room(channel="cli", target=target)
    sessions.add_participant(
        session_id=room.id,
        participant_type="human",
        participant_id="owner",
        role="owner",
    )
    return room.id


def join_agent(peer: SessionStore, session_id: str, agent: str) -> RoomParticipant:
    return peer.add_participant(
        session_id=session_id, participant_type="agent", participant_id=agent
    )


def join_and_read_active(peer: SessionStore, session_id: str, agent: str) -> str | None:
    """Join `agent`, then read the active agent as the join's caller sees it."""
    join_agent(peer, session_id, agent)
    return peer.get_active_agent(session_id)


def agent_ids(sessions: SessionStore, session_id: str) -> list[str]:
    return [
        item.participant_id
        for item in sessions.list_participants(session_id)
        if item.participant_type == "agent"
    ]


def stored_active_agent(sessions: SessionStore, session_id: str) -> str | None:
    session = sessions.get_session(session_id)
    assert session is not None
    return session.active_agent_id


def check_join_keeps_an_activation_committed_during_it(
    sessions: SessionStore, first: SessionStore, second: SessionStore
) -> None:
    """Two first joins on two connections (#393, case 1).

    `alpha` joins on `first` and is held after reading that the room has no
    active agent; `beta` then joins on `second`. The join of `beta` waits for
    the held join to commit, finds `alpha` active and leaves it active, so
    each caller sees the agent that stays active. Before #393 `beta` joined
    and was activated during the hold, its caller saw `beta` active, and the
    held join then replaced it with `alpha`.
    """
    room_id = new_room(sessions, "two-first-joins")
    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        held_activation_read(first) as hold,
    ):
        alpha = pool.submit(join_and_read_active, first, room_id, "alpha")
        assert hold.read.wait(timeout=HOLD_TIMEOUT)
        beta = pool.submit(join_and_read_active, second, room_id, "beta")
        beta_finished = finished_while_held(beta)
        hold.release.set()
        seen = {"alpha": alpha.result(timeout=30), "beta": beta.result(timeout=30)}

    assert hold.seen is None, hold.seen
    # What each caller saw after its join, and the stored active agent.
    outcome = (seen, stored_active_agent(sessions, room_id))
    assert outcome == ({"alpha": "alpha", "beta": "alpha"}, "alpha"), outcome
    assert agent_ids(sessions, room_id) == ["alpha", "beta"]
    assert not beta_finished, "the join of beta did not wait for the held join"


def check_selection_during_a_join_applies_after_it(
    sessions: SessionStore, first: SessionStore, second: SessionStore
) -> None:
    """An explicit selection during a first join (#393, case 2).

    `alpha` joins on `first` and is held after reading that the room has no
    active agent; on `second`, `beta` then joins and is selected with
    `set_active_agent`. They wait for the held join to commit, and the
    selection then applies. Before #393 the held join wrote `alpha` over the
    selection.
    """
    room_id = new_room(sessions, "selection")

    def join_and_select_beta() -> str | None:
        join_agent(second, room_id, "beta")
        selected = second.set_active_agent(session_id=room_id, agent_id="beta")
        return selected.active_agent_id

    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        held_activation_read(first) as hold,
    ):
        alpha = pool.submit(join_agent, first, room_id, "alpha")
        assert hold.read.wait(timeout=HOLD_TIMEOUT)
        selection = pool.submit(join_and_select_beta)
        selection_finished = finished_while_held(selection)
        hold.release.set()
        alpha.result(timeout=30)
        selected = selection.result(timeout=30)

    assert hold.seen is None, hold.seen
    # What the selection returned, and the stored active agent.
    outcome = (selected, stored_active_agent(sessions, room_id))
    assert outcome == ("beta", "beta"), outcome
    assert sessions.get_active_agent(room_id) == "beta"
    assert agent_ids(sessions, room_id) == ["alpha", "beta"]
    assert not selection_finished, "the selection did not wait for the held join"


def check_leave_during_a_join_applies_after_it(
    sessions: SessionStore, first: SessionStore, second: SessionStore
) -> None:
    """The joining agent leaves during its own first join (#393, case 3).

    `alpha` joins on `first` and is held after reading that the room has no
    active agent; on `second`, `alpha` then leaves. The leave waits for the
    held join to commit and then applies: `alpha` is no longer a participant
    and no agent is active. The held join reads its participant back only
    after the leave has returned, so it raises as it did before #393. Before
    #393 the held join stored `alpha`, which had left, as the active agent.
    """
    room_id = new_room(sessions, "leave")
    left = threading.Event()
    owner = first._participants
    get_participant = owner.get_participant

    def get_participant_after_the_leave(*args: Any, **kwargs: Any) -> Any:
        assert left.wait(timeout=HOLD_TIMEOUT)
        return get_participant(*args, **kwargs)

    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        held_activation_read(first) as hold,
        patch.object(owner, "get_participant", get_participant_after_the_leave),
    ):
        alpha = pool.submit(join_agent, first, room_id, "alpha")
        assert hold.read.wait(timeout=HOLD_TIMEOUT)
        leave = pool.submit(
            second.remove_participant,
            session_id=room_id,
            participant_type="agent",
            participant_id="alpha",
        )
        leave_finished = finished_while_held(leave)
        hold.release.set()
        assert leave.result(timeout=30) is True
        left.set()
        with pytest.raises(RuntimeError, match="Failed to upsert participant"):
            alpha.result(timeout=30)

    assert hold.seen is None, hold.seen
    # The agents listed, and the stored active agent.
    outcome = (agent_ids(sessions, room_id), stored_active_agent(sessions, room_id))
    assert outcome == ([], None), outcome
    assert sessions.get_active_agent(room_id) is None
    with pytest.raises(ValueError, match="not an active participant"):
        sessions.set_active_agent(session_id=room_id, agent_id="alpha")
    assert not leave_finished, "the leave did not wait for the held join"


def check_joins_selection_and_leaves_on_two_connections(
    sessions: SessionStore, first: SessionStore, second: SessionStore
) -> None:
    """Joins, a selection and leaves on two connections, one after another."""
    room_id = new_room(sessions, "one-after-another")
    # The human owner activates nobody; the first agent join does.
    assert stored_active_agent(sessions, room_id) is None
    join_agent(first, room_id, "alpha")
    assert second.get_active_agent(room_id) == "alpha"
    join_agent(second, room_id, "beta")
    assert stored_active_agent(sessions, room_id) == "alpha"

    selected = second.set_active_agent(session_id=room_id, agent_id="beta")
    assert selected.active_agent_id == "beta"
    assert first.get_active_agent(room_id) == "beta"
    join_agent(first, room_id, "alpha")
    assert stored_active_agent(sessions, room_id) == "beta"

    # The active agent leaving selects the earliest remaining agent join; the
    # last agent leaving leaves no active agent, and the next join activates.
    assert second.remove_participant(
        session_id=room_id, participant_type="agent", participant_id="beta"
    )
    assert first.get_active_agent(room_id) == "alpha"
    # The agent that left cannot be selected on the other connection.
    with pytest.raises(ValueError, match="not an active participant"):
        first.set_active_agent(session_id=room_id, agent_id="beta")
    assert stored_active_agent(sessions, room_id) == "alpha"
    assert first.remove_participant(
        session_id=room_id, participant_type="agent", participant_id="alpha"
    )
    assert stored_active_agent(sessions, room_id) is None
    join_agent(second, room_id, "gamma")
    assert stored_active_agent(sessions, room_id) == "gamma"
    assert first.get_active_agent(room_id) == "gamma"


def check_legacy_owner_records_activate_no_agent(sessions: SessionStore) -> None:
    """A legacy session's owner record is written without activating an agent.

    The record is written when the session's participants are first read, or
    when another agent joins. The stored active agent stays as it was, and
    `get_active_agent` still answers the session's own agent until an agent
    join activates one.
    """
    read = sessions.resolve_session(
        agent_id="legacy-agent", channel="console", target="legacy-read"
    )
    assert participant_ids(sessions, read.id) == ["legacy-agent"]
    assert stored_active_agent(sessions, read.id) is None
    assert sessions.get_active_agent(read.id) == "legacy-agent"

    invited = sessions.resolve_session(
        agent_id="legacy-agent", channel="console", target="legacy-invite"
    )
    join_agent(sessions, invited.id, "guest-agent")
    assert participant_ids(sessions, invited.id) == ["legacy-agent", "guest-agent"]
    assert stored_active_agent(sessions, invited.id) == "guest-agent"

    own = sessions.resolve_session(
        agent_id="legacy-agent", channel="console", target="legacy-own"
    )
    join_agent(sessions, own.id, "legacy-agent")
    assert stored_active_agent(sessions, own.id) == "legacy-agent"


def check_blank_active_agent_counts_as_none(sessions: SessionStore) -> None:
    """An empty or blank stored active agent counts as no active agent."""
    for target, blank in (("blank-empty", ""), ("blank-spaces", "   ")):
        room_id = new_room(sessions, target)
        sessions._backend.execute_count(
            "UPDATE sessions SET active_agent_id = ? WHERE id = ?", (blank, room_id)
        )
        row = sessions._backend.query_one(
            "SELECT active_agent_id FROM sessions WHERE id = ?", (room_id,)
        )
        assert row is not None and row["active_agent_id"] == blank
        join_agent(sessions, room_id, "alpha")
        assert stored_active_agent(sessions, room_id) == "alpha"


SELECTION_WRITE = "UPDATE sessions SET active_agent_id"


@contextmanager
def held_selection_write(peer: SessionStore) -> Iterator[threading.Event]:
    """Hold the next selection on `peer` right before its active-agent write.

    The event is set once `set_active_agent` has checked the agent's
    membership and sends `UPDATE sessions SET active_agent_id`. The write
    runs when the context exits.
    """
    backend = peer._participants._backend
    execute_count = backend.execute_count
    held, release = threading.Event(), threading.Event()

    def execute_count_held_before_the_write(sql: str, params: Any = None) -> int:
        if sql.lstrip().startswith(SELECTION_WRITE) and not held.is_set():
            held.set()
            assert release.wait(timeout=HOLD_TIMEOUT)
        return execute_count(sql, params)

    with patch.object(backend, "execute_count", execute_count_held_before_the_write):
        try:
            yield held
        finally:
            release.set()


@contextmanager
def held_leave_fallback(peer: SessionStore) -> Iterator[threading.Event]:
    """Hold the next leave on `peer` right after it marks its participant left.

    The event is set once `remove_participant` reads the active agent for its
    fallback, in its open transaction, under the session-row lock. The leave
    goes on when the context exits.
    """
    owner = peer._participants
    get_active_agent = owner.get_active_agent
    held, release = threading.Event(), threading.Event()

    def get_active_agent_held_after_the_leave(session_id: str) -> str | None:
        if not held.is_set():
            held.set()
            assert release.wait(timeout=HOLD_TIMEOUT)
        return get_active_agent(session_id)

    with patch.object(owner, "get_active_agent", get_active_agent_held_after_the_leave):
        try:
            yield held
        finally:
            release.set()


def select_agent(peer: SessionStore, session_id: str, agent: str) -> str | None:
    """Select `agent`: the active agent the selection returned, or `"refused"`."""
    try:
        selected = peer.set_active_agent(session_id=session_id, agent_id=agent)
    except ValueError as exc:
        assert "not an active participant" in str(exc), exc
        return "refused"
    return selected.active_agent_id


def check_leave_during_a_selection_applies_after_it(
    sessions: SessionStore, selector: SessionStore, leaver: SessionStore
) -> None:
    """`beta` leaves while it is being selected (#495, case 1).

    `beta` is selected on `selector` and held after its membership check,
    before its write; on `leaver`, `beta` then leaves. The leave waits for
    the selection to commit, finds `beta` active and runs its fallback.
    Before #495 the leave committed during the hold, and the selection then
    stored `beta`, which had left.
    """
    room_id = new_room(sessions, "leave-during-selection")
    join_agent(sessions, room_id, "alpha")
    join_agent(sessions, room_id, "beta")
    with ThreadPoolExecutor(max_workers=2) as pool:
        with held_selection_write(selector) as held:
            selection = pool.submit(select_agent, selector, room_id, "beta")
            assert held.wait(timeout=HOLD_TIMEOUT)
            leave = pool.submit(
                leaver.remove_participant,
                session_id=room_id,
                participant_type="agent",
                participant_id="beta",
            )
            leave_finished = finished_while_held(leave)
        selected = selection.result(timeout=30)
        assert leave.result(timeout=30) is True

    # What the selection returned, the agents listed, and the stored agent.
    stored = stored_active_agent(sessions, room_id)
    outcome = (selected, agent_ids(sessions, room_id), stored)
    assert outcome == ("beta", ["alpha"], "alpha"), outcome
    assert sessions.get_active_agent(room_id) == "alpha"
    assert not leave_finished, "the leave did not wait for the held selection"


def check_selection_during_a_leave_is_refused(
    sessions: SessionStore, selector: SessionStore, leaver: SessionStore
) -> None:
    """`beta` is selected while it leaves (#495, case 2).

    `beta` leaves on `leaver` and is held after it is marked as left, before
    the fallback, in the leave's open transaction; on `selector`, `beta` is
    then selected. The selection waits for the leave to commit, finds that
    `beta` has left and is refused. Before #495 the selection's check passed
    during the hold, and its write stored `beta` after the leave committed.
    """
    room_id = new_room(sessions, "selection-during-leave")
    join_agent(sessions, room_id, "alpha")
    join_agent(sessions, room_id, "beta")
    with ThreadPoolExecutor(max_workers=2) as pool:
        with held_leave_fallback(leaver) as held:
            leave = pool.submit(
                leaver.remove_participant,
                session_id=room_id,
                participant_type="agent",
                participant_id="beta",
            )
            assert held.wait(timeout=HOLD_TIMEOUT)
            selection = pool.submit(select_agent, selector, room_id, "beta")
            selection_finished = finished_while_held(selection)
        assert leave.result(timeout=30) is True
        selected = selection.result(timeout=30)

    # What the selection returned, the agents listed, and the stored agent.
    stored = stored_active_agent(sessions, room_id)
    outcome = (selected, agent_ids(sessions, room_id), stored)
    assert outcome == ("refused", ["alpha"], "alpha"), outcome
    assert sessions.get_active_agent(room_id) == "alpha"
    assert not selection_finished, "the selection did not wait for the held leave"


def check_legacy_session_selection(sessions: SessionStore) -> None:
    """A legacy session keeps its selection rules (#495).

    Another agent is refused until it joins, and the refusal stores no active
    agent; the session's own agent can be selected, also after its owner
    record has left.
    """
    session = sessions.resolve_session(
        agent_id="legacy-agent", channel="console", target="legacy-selection"
    )
    with pytest.raises(ValueError, match="bound to agent 'legacy-agent'"):
        sessions.set_active_agent(session_id=session.id, agent_id="guest-agent")
    assert stored_active_agent(sessions, session.id) is None

    join_agent(sessions, session.id, "guest-agent")
    selected = sessions.set_active_agent(session_id=session.id, agent_id="guest-agent")
    assert selected.active_agent_id == "guest-agent"
    selected = sessions.set_active_agent(session_id=session.id, agent_id="legacy-agent")
    assert selected.active_agent_id == "legacy-agent"

    remove_agent(sessions, session.id, "legacy-agent")
    assert participant_ids(sessions, session.id) == ["guest-agent"]
    assert stored_active_agent(sessions, session.id) == "guest-agent"
    selected = sessions.set_active_agent(session_id=session.id, agent_id="legacy-agent")
    assert selected.active_agent_id == "legacy-agent"


def legacy_participant_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sessions and participants as stored before runtime migration 14.

    In insertion order. Inside each joined_at the ids sort in reverse of that
    order, and `a-left` has left.
    """
    sessions = [
        {
            "id": session_id,
            "session_key": build_room_session_key(session_id=session_id),
            "channel": "cli",
            "target": session_id,
            "active_agent_id": active,
        }
        for session_id, active in (("room-a", "a-early"), ("room-b", "b-first"))
    ]
    participants = [
        {
            "id": char * 32,
            "session_id": session_id,
            "participant_type": "agent",
            "participant_id": participant_id,
            "joined_at": joined_at,
            "left_at": left_at,
        }
        for char, session_id, participant_id, joined_at, left_at in (
            ("f", "room-a", "a-first", TIED_AT, None),
            ("e", "room-b", "b-first", TIED_AT, None),
            ("c", "room-a", "a-second", TIED_AT, None),
            ("5", "room-a", "a-early", EARLIER_AT, None),
            ("8", "room-a", "a-left", TIED_AT, LATER_AT),
            ("0", "room-a", "a-third", TIED_AT, None),
            ("4", "room-b", "b-later", LATER_AT, None),
        )
    ]
    return sessions, participants


# The pre-upgrade read order of `legacy_participant_rows`: joined_at, then id.
LEGACY_ORDER = {
    "room-a": ["a-early", "a-third", "a-second", "a-first"],
    "room-b": ["b-first", "b-later"],
}


def check_upgraded_join_order(sessions: SessionStore, counted: dict[str, int]) -> None:
    """After migration 14 on `legacy_participant_rows`.

    `counted` is `COUNTED_JOIN_SEQ_SQL` by row id, read before the upgrade.
    """
    stored = sessions._backend.query_dicts(
        "SELECT id, join_seq FROM room_participants", ()
    )
    assert {str(row["id"]): int(row["join_seq"]) for row in stored} == counted
    # Every row, including the one that left, is numbered per session in the
    # pre-upgrade read order, so the listings and fallbacks do not change.
    assert join_seq_by_participant(sessions, "room-a") == {
        "a-early": 1,
        "a-third": 2,
        "a-left": 3,
        "a-second": 4,
        "a-first": 5,
    }
    assert join_seq_by_participant(sessions, "room-b") == {
        "b-first": 1,
        "b-later": 2,
    }
    for session_id, order in LEGACY_ORDER.items():
        assert participant_ids(sessions, session_id) == order
    remove_agent(sessions, "room-a", "a-early")
    assert sessions.get_active_agent("room-a") == "a-third"

    # Later joins and rejoins on the tied timestamp go after the existing
    # rows; joined_at still comes first.
    join_agents(sessions, "room-a", ("a-new", "a-left"), ids="12")
    assert participant_ids(sessions, "room-a") == [
        "a-third",
        "a-second",
        "a-first",
        "a-new",
        "a-left",
    ]
    assert join_seq_by_participant(sessions, "room-a")["a-new"] == 6
    assert join_seq_by_participant(sessions, "room-a")["a-left"] == 7
    join_agents(sessions, "room-b", ("b-new",), ids="3")
    assert participant_ids(sessions, "room-b") == ["b-first", "b-new", "b-later"]
    assert join_seq_by_participant(sessions, "room-b")["b-new"] == 3
