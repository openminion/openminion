from pathlib import Path
from typing import Any
from collections.abc import Callable

from .modes import mode_is_local, raise_if_strict


def create_tool_adapter(
    mode: str = "auto",
    workspace: str | Path | None = None,
    runtime_config: Any = None,
    runtime_registry: Any | None = None,
    policy: Any = None,
    policy_adapter: Any = None,
    policy_ctl: Any = None,
    reactions_enabled: bool = True,
    skill_api: Any | None = None,
    secret_service: Any | None = None,
    commerce_runtime: Any | None = None,
    memory_service: Any | None = None,
    knowledge_graph_service: Any | None = None,
    ops_service: Any | None = None,
    a2a_delegate_api: Any | None = None,
    agent_query: Any | None = None,
    agent_id: str | None = None,
    agent_profile: Any | None = None,
    task_manager: Any | None = None,
    scheduler_readiness: Callable[[], dict[str, Any]] | None = None,
    telemetryctl: Any | None = None,
    artifactctl: Any | None = None,
    sandbox_runner: Any | None = None,
    security_lab_runner: Any | None = None,
    identity_security_lab_facts: Callable[[], dict[str, Any]] | None = None,
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
            policy_ctl=policy_ctl,
            reactions_enabled=reactions_enabled,
            skill_api=skill_api,
            secret_service=secret_service,
            commerce_runtime=commerce_runtime,
            memory_service=memory_service,
            knowledge_graph_service=knowledge_graph_service,
            ops_service=ops_service,
            a2a_delegate_api=a2a_delegate_api,
            agent_query=agent_query,
            agent_id=agent_id,
            agent_profile=agent_profile,
            task_manager=task_manager,
            scheduler_readiness=scheduler_readiness,
            telemetryctl=telemetryctl,
            artifactctl=artifactctl,
            sandbox_runner=sandbox_runner,
            security_lab_runner=security_lab_runner,
            identity_security_lab_facts=identity_security_lab_facts,
        )
    except ImportError:
        raise_if_strict(mode)
        return LocalToolAdapter()
