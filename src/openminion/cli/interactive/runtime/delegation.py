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
        approval_callback: Any | None = None,
    ) -> dict[str, Any]:
        from openminion.cli.commands.agent.delegation import (
            AgentDelegateRequest,
            run_agent_delegate_request,
        )

        return run_agent_delegate_request(
            config=self._rt.config,
            home_root=self._rt.home_root,
            parent_agent_id=self.agent_id,
            runtime_resolver=lambda: self._rt,
            approval_callback=approval_callback,
            workspace_root=self.working_dir,
            cwd=self.working_dir,
            request=AgentDelegateRequest(
                mode=mode,
                target_agent_id=target_agent_id,
                instruction=instruction,
                task_id=task_id,
                timeout_seconds=timeout_seconds,
            ),
        )


__all__ = ["RuntimeDelegationMixin"]
