from typing import Any

from openminion.cli.interactive.models import SidebarItem
from openminion.modules.storage import is_room_session_key


def build_agent_sidebar_items(
    runtime: Any, *, active_agent_id: str
) -> list[SidebarItem]:
    snapshot = getattr(runtime, "agent_discovery_snapshot", None)
    if callable(snapshot):
        return [
            SidebarItem(
                str(item.get("agent_id", "")),
                str(item.get("display_name") or item.get("agent_id", "")),
                active=(str(item.get("agent_id", "")) == active_agent_id),
                meta=dict(item),
            )
            for item in snapshot()
            if str(item.get("agent_id", "")).strip()
        ]

    return [
        SidebarItem(agent_id, agent_id, active=(agent_id == active_agent_id))
        for agent_id in runtime.list_registered_agents()
    ]


def build_session_sidebar_item(
    runtime: Any, session: Any, *, active_session_id: str | None
) -> SidebarItem:
    metadata = dict(getattr(session, "metadata", {}) or {})
    room = is_room_session_key(str(getattr(session, "session_key", "") or ""))
    participants = (
        list(runtime._rt.sessions.list_participants(session.id)) if room else []
    )
    preview_records = runtime._rt.sessions.list_messages(session_id=session.id, limit=3)
    preview_lines = [
        f"{runtime._role_to_sender(str(getattr(record, 'role', '') or '').strip().lower(), getattr(record, 'metadata', {}) or {})}: "
        f"{str(getattr(record, 'body', '') or '')[:40]}"
        for record in preview_records
        if str(getattr(record, "body", "") or "").strip()
    ]
    return SidebarItem(
        id=session.id,
        label=str(metadata.get("name") or session.id[:12]) if room else session.id[:12],
        active=session.id == active_session_id,
        meta={
            "channel": session.channel,
            "target": session.target,
            "status": session.status,
            "updated_at": session.updated_at,
            "preview_lines": preview_lines,
            "session_type": runtime._classify_session_type(session),
            "room_routing_mode": metadata.get("room_routing_mode", ""),
            "local_human_id": metadata.get("local_human_id", ""),
            "participant_count": len(participants),
            "participant_roles": [
                f"{item.participant_id}:{item.role}" for item in participants
            ],
            "active_agent_id": session.active_agent_id,
        },
    )
