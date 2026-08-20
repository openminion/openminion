from typing import Any

from openminion.cli.interactive.models import SidebarItem


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
