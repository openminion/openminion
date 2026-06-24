from pathlib import Path
from typing import Any

from .modes import mode_is_local, raise_if_strict
from .environment import ensure_a2a_dependency_available


def create_a2a_adapter(
    mode: str = "auto",
    *,
    home_root: str | Path | None = None,
    config: Any = None,
    agent_id: str | None = None,
    env: Any = None,
    runtime_resolver: Any = None,
) -> Any:
    from openminion.modules.brain.adapters.a2a import LocalA2AAdapter

    if mode_is_local(mode):
        return LocalA2AAdapter()
    try:
        ensure_a2a_dependency_available()
        from ..a2a import A2actlAdapter

        return A2actlAdapter(
            home_root=home_root,
            config=config,
            agent_id=agent_id,
            env=env,
            runtime_resolver=runtime_resolver,
        )
    except ImportError:
        raise_if_strict(mode)
        return LocalA2AAdapter()
