from pathlib import Path
from typing import Any

from .modes import mode_is_local, raise_if_strict


def create_tool_adapter(
    mode: str = "auto",
    workspace: str | Path | None = None,
    runtime_config: Any = None,
    runtime_registry: Any | None = None,
    policy: Any = None,
    policy_adapter: Any = None,
    reactions_enabled: bool = True,
    skill_api: Any | None = None,
    agent_id: str | None = None,
    agent_profile: Any | None = None,
) -> Any:
    from openminion.modules.brain.adapters.tool import LocalToolAdapter

    if mode_is_local(mode):
        return LocalToolAdapter()
    try:
        from ..tool import ToolAdapter

        return ToolAdapter(
            workspace_root=Path(workspace or "."),
            runtime_config=runtime_config,
            runtime_registry=runtime_registry,
            policy=policy,
            policy_adapter=policy_adapter,
            reactions_enabled=reactions_enabled,
            skill_api=skill_api,
            agent_id=agent_id,
            agent_profile=agent_profile,
        )
    except ImportError:
        raise_if_strict(mode)
        return LocalToolAdapter()
