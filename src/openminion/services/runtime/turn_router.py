import re
from dataclasses import dataclass
from typing import Any, Iterable

from openminion.modules.storage.runtime.session_store import RoomParticipant

_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9._:-]+)")


@dataclass(frozen=True)
class TurnRoutingDecision:
    mode: str
    agent_ids: tuple[str, ...]


class TurnRouter:
    def route(
        self,
        *,
        session: Any | None,
        message: str,
        participants: Iterable[RoomParticipant],
        requested_agent_id: str,
    ) -> TurnRoutingDecision:
        active_agents = [
            participant
            for participant in participants
            if participant.participant_type == "agent" and participant.left_at is None
        ]
        if not active_agents:
            return TurnRoutingDecision(
                mode="addressed",
                agent_ids=(str(requested_agent_id or "").strip(),),
            )

        metadata = getattr(session, "metadata", {}) or {}
        mode = str(metadata.get("room_routing_mode", "") or "addressed").strip().lower()
        if mode not in {"addressed", "broadcast", "sequential"}:
            mode = "addressed"

        if mode == "broadcast":
            return TurnRoutingDecision(
                mode=mode,
                agent_ids=tuple(item.participant_id for item in active_agents),
            )
        if mode == "sequential":
            order = metadata.get("room_routing_order", [])
            ordered = _apply_sequential_order(active_agents, order)
            return TurnRoutingDecision(
                mode=mode,
                agent_ids=tuple(item.participant_id for item in ordered),
            )

        mentions = {
            match.group(1).strip().lower()
            for match in _MENTION_PATTERN.finditer(str(message or ""))
            if match.group(1).strip()
        }
        if mentions:
            addressed = [
                item.participant_id
                for item in active_agents
                if item.participant_id.lower() in mentions
            ]
            if addressed:
                return TurnRoutingDecision(mode=mode, agent_ids=tuple(addressed))

        active_agent_id = str(getattr(session, "active_agent_id", "") or "").strip()
        if active_agent_id:
            for item in active_agents:
                if item.participant_id == active_agent_id:
                    return TurnRoutingDecision(mode=mode, agent_ids=(active_agent_id,))

        requested = str(requested_agent_id or "").strip()
        if requested:
            for item in active_agents:
                if item.participant_id == requested:
                    return TurnRoutingDecision(mode=mode, agent_ids=(requested,))

        return TurnRoutingDecision(
            mode=mode, agent_ids=(active_agents[0].participant_id,)
        )


def _apply_sequential_order(
    participants: list[RoomParticipant],
    order: Any,
) -> list[RoomParticipant]:
    requested_order = [
        str(item).strip()
        for item in (order if isinstance(order, list) else [])
        if str(item).strip()
    ]
    if not requested_order:
        return participants
    by_id = {participant.participant_id: participant for participant in participants}
    ordered: list[RoomParticipant] = []
    seen: set[str] = set()
    for participant_id in requested_order:
        participant = by_id.get(participant_id)
        if participant is None:
            continue
        ordered.append(participant)
        seen.add(participant_id)
    for participant in participants:
        if participant.participant_id in seen:
            continue
        ordered.append(participant)
    return ordered
