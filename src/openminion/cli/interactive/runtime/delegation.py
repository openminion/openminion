from typing import Any


class RuntimeDelegationMixin:
    """Delegation commands exposed by interactive runtimes."""

    _rt: Any
    agent_id: str
    working_dir: str

    def delegate_task(
        self,
        *,
        mode: str,
        target_agent_id: str = "",
        instruction: str = "",
        task_id: str = "",
        timeout_seconds: int = 120,
        child_artifact: dict[str, Any] | None = None,
        workspace_root: str = "",
        approval_callback: Any | None = None,
    ) -> dict[str, Any]:
        from openminion.cli.commands.agent.delegation import (
            AgentDelegateRequest,
            run_agent_delegate_request,
        )

        agent_service = self._rt.resolve_agent_service(self.agent_id)
        return run_agent_delegate_request(
            config=self._rt.config,
            home_root=self._rt.home_root,
            parent_agent_id=self.agent_id,
            runtime_resolver=lambda: self._rt,
            approval_callback=approval_callback,
            workspace_root=self.working_dir,
            cwd=self.working_dir,
            artifactctl=getattr(agent_service, "artifactctl", None),
            request=AgentDelegateRequest(
                mode=mode,
                target_agent_id=target_agent_id,
                instruction=instruction,
                task_id=task_id,
                timeout_seconds=timeout_seconds,
                child_artifact=child_artifact,
                workspace_root=workspace_root or self.working_dir,
            ),
        )


__all__ = ["RuntimeDelegationMixin"]
