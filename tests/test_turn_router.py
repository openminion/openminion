from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.storage.runtime.session_store import RoomParticipant
from openminion.services.runtime.turn_router import TurnRouter


def _participant(
    agent_id: str, *, joined_at: str, role: str = "participant"
) -> RoomParticipant:
    return RoomParticipant(
        id=f"p-{agent_id}",
        session_id="room-1",
        participant_type="agent",
        participant_id=agent_id,
        channel="cli",
        role=role,
        display_name=agent_id,
        joined_at=joined_at,
        left_at=None,
    )


def test_turn_router_addressed_prefers_mentions() -> None:
    router = TurnRouter()
    session = SimpleNamespace(active_agent_id="writer-agent", metadata={})
    participants = [
        _participant("writer-agent", joined_at="2026-04-02T00:00:00Z"),
        _participant("review-agent", joined_at="2026-04-02T00:00:01Z"),
    ]

    decision = router.route(
        session=session,
        message="@review-agent please check this",
        participants=participants,
        requested_agent_id="writer-agent",
    )

    assert decision.mode == "addressed"
    assert decision.agent_ids == ("review-agent",)


def test_turn_router_broadcast_returns_all_active_agents() -> None:
    router = TurnRouter()
    session = SimpleNamespace(
        active_agent_id="writer-agent",
        metadata={"room_routing_mode": "broadcast"},
    )
    participants = [
        _participant("writer-agent", joined_at="2026-04-02T00:00:00Z"),
        _participant("review-agent", joined_at="2026-04-02T00:00:01Z"),
    ]

    decision = router.route(
        session=session,
        message="everyone respond",
        participants=participants,
        requested_agent_id="writer-agent",
    )

    assert decision.mode == "broadcast"
    assert decision.agent_ids == ("writer-agent", "review-agent")


def test_turn_router_sequential_respects_explicit_order() -> None:
    router = TurnRouter()
    session = SimpleNamespace(
        active_agent_id="writer-agent",
        metadata={
            "room_routing_mode": "sequential",
            "room_routing_order": ["review-agent", "writer-agent"],
        },
    )
    participants = [
        _participant("writer-agent", joined_at="2026-04-02T00:00:00Z"),
        _participant("review-agent", joined_at="2026-04-02T00:00:01Z"),
    ]

    decision = router.route(
        session=session,
        message="please review in order",
        participants=participants,
        requested_agent_id="writer-agent",
    )

    assert decision.mode == "sequential"
    assert decision.agent_ids == ("review-agent", "writer-agent")


def test_turn_router_addressed_falls_back_to_active_agent() -> None:
    router = TurnRouter()
    session = SimpleNamespace(active_agent_id="writer-agent", metadata={})
    participants = [
        _participant("writer-agent", joined_at="2026-04-02T00:00:00Z"),
        _participant("review-agent", joined_at="2026-04-02T00:00:01Z"),
    ]

    decision = router.route(
        session=session,
        message="please continue",
        participants=participants,
        requested_agent_id="review-agent",
    )

    assert decision.agent_ids == ("writer-agent",)
