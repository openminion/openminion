from __future__ import annotations

from typing import Any


class RuntimeDelegationMixin:
    """Delegation commands exposed by interactive runtimes."""

    def delegate_task(
        self,
        *,
        mode: str,
        target_agent_id: str = "",
        instruction: str = "",
        task_id: str = "",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        from openminion.cli.commands.agent_delegation import (
            AgentDelegateRequest,
            run_agent_delegate_request,
        )

        return run_agent_delegate_request(
            config=self._rt.config,
            home_root=self._rt.home_root,
            parent_agent_id=self.agent_id,
            runtime_resolver=lambda: self._rt,
            request=AgentDelegateRequest(
                mode=mode,
                target_agent_id=target_agent_id,
                instruction=instruction,
                task_id=task_id,
                timeout_seconds=timeout_seconds,
            ),
        )


__all__ = ["RuntimeDelegationMixin"]
