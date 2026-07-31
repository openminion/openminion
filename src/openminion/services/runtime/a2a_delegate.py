from pathlib import Path
from typing import Any

from openminion.base.logging import get_logger
from openminion.modules.tool.runtime.delegation import (
    A2ADelegateApi,
    A2aRuntimeDelegateAdapter,
)

_LOG = get_logger("services.runtime.a2a_delegate")


def build_a2a_delegate_api(
    *,
    config: Any,
    home_root: str | Path | None,
    agent_id: str,
    env: Any = None,
    mode: str = "auto",
    runtime_resolver: Any = None,
    approval_callback: Any | None = None,
) -> A2ADelegateApi | None:
    """Build a2a delegate api helper."""
    try:
        from openminion.modules.brain.adapters.factory.a2a import create_a2a_adapter
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("A2A delegate adapter factory import failed: %s", exc)
        return None
    try:
        a2actl = create_a2a_adapter(
            mode,
            home_root=home_root,
            config=config,
            agent_id=str(agent_id or "").strip() or None,
            env=env,
            runtime_resolver=runtime_resolver,
            approval_callback=approval_callback,
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("A2A delegate adapter construction failed: %s", exc)
        return None
    call = getattr(a2actl, "call", None)
    if not callable(call):
        return None
    return A2aRuntimeDelegateAdapter(a2a_call=call, parent_agent_id=str(agent_id or ""))


__all__ = ["A2aRuntimeDelegateAdapter", "build_a2a_delegate_api"]
