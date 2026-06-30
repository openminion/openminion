from typing import Optional

from openminion.api.config import close_api_runtime_if_owned
from openminion.api.core.deps import resolve_runtime_manager


def evict_agent_runtime(
    *,
    config_path: Optional[str],
    runtime,
    agent_id: str,
    reason: str,
) -> dict[str, object]:
    manager, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        manager.evict(agent_id, reason)
        return {
            "ok": True,
            "agent_id": agent_id,
            "evicted": True,
            "reason": reason,
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)
