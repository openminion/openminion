"""Room participant join-order scenarios shared by the SQLite and Postgres tests (#364).

The scenarios join participants with the participant clock patched to one
timestamp and the participant row ids patched so that each id sorts before the
id of the participant that joined before it, as in the issue. The contract:

- `list_participants` returns the active participants in join order;
- a participant added again while active keeps its place and `joined_at`;
- a participant that leaves and rejoins takes a new place, after every earlier
  join, with the rejoin's `joined_at`;
- an explicitly selected active agent stays selected;
- when the active agent leaves, the earliest remaining agent join is selected.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
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
