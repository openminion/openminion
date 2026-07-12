from pathlib import Path
from typing import Any

from openminion.base.config import ActionPolicyConfig
from openminion.base.config.env import EnvironmentConfig
from openminion.modules.brain.adapters.factory import (
    create_a2a_adapter,
    create_compress_adapter,
    create_context_adapter,
    create_memory_adapter,
    create_policy_adapter,
    create_safety_adapter,
    create_session_adapter,
    create_skill_adapter,
    create_tool_adapter,
)
from openminion.services.agent.context.history import _resolve_system_prompt


def create_session_api(
    *,
    mode: str,
    db_path: str,
    telemetryctl: Any | None = None,
) -> Any:
    return create_session_adapter(mode=mode, db_path=db_path, telemetryctl=telemetryctl)


def create_tool_api(
    *,
    mode: str,
    workspace_root: str,
    runtime_config: Any,
    runtime_registry: Any | None = None,
    agent_name: str | None = None,
    skill_api: Any | None = None,
    agent_profile: Any | None = None,
) -> Any:
    return create_tool_adapter(
        mode=mode,
        workspace=workspace_root,
        runtime_config=runtime_config,
        runtime_registry=runtime_registry,
        policy=None,
        policy_adapter=None,
        reactions_enabled=getattr(runtime_config, "reactions_enabled", True),
        agent_id=str(agent_name or "").strip() or None,
        skill_api=skill_api,
        agent_profile=agent_profile,
    )


def create_a2a_api(
    *,
    mode: str,
    home_root: Path,
    agent_name: str,
    config: Any | None,
    env: EnvironmentConfig | None = None,
    runtime_resolver: Any = None,
) -> Any:
    return create_a2a_adapter(
        mode=mode,
        home_root=home_root,
        agent_id=agent_name.strip() or None,
        config=config,
        env=env,
        runtime_resolver=runtime_resolver,
    )


def create_context_api(
    *,
    mode: str,
    session_store: Any,
    system_prompt: str,
    identity_budget_config: Any | None = None,
    runtime_token_budget: int | None = None,
    vector_adapter: Any | None = None,
    telemetryctl: Any | None = None,
    skill_config: Any | None = None,
    skill_home_root: Any | None = None,
) -> Any:
    return create_context_adapter(
        mode=mode,
        session_store=session_store,
        identity_system_prompt=_resolve_system_prompt(system_prompt),
        identity_budget_config=identity_budget_config,
        runtime_token_budget=runtime_token_budget,
        vectorctl=vector_adapter,
        vector_adapter=vector_adapter,
        telemetryctl=telemetryctl,
        skill_config=skill_config,
        skill_home_root=skill_home_root,
    )


def create_memory_api(
    *,
    mode: str,
    db_dir: Path,
    config: Any | None,
    vector_adapter: Any | None,
    telemetryctl: Any | None = None,
    agent_id: str | None = None,
) -> Any:
    return create_memory_adapter(
        mode=mode,
        db_path=None if config is not None else db_dir,
        vector_adapter=vector_adapter,
        config=config,
        telemetryctl=telemetryctl,
        agent_id=agent_id,
    )


def create_policy_api(
    *,
    mode: str,
    db_dir: Path,
    policy_service: Any | None = None,
    action_policy_config: ActionPolicyConfig | None = None,
) -> Any:
    if policy_service is not None and mode != "local":
        from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter

        return PolicyCtlBrainAdapter(
            policy_service,
            action_policy_config=action_policy_config,
        )
    return create_policy_adapter(mode=mode, db_path=db_dir)


def create_safety_api(*, mode: str) -> Any:
    return create_safety_adapter(mode=mode)


def create_compress_api(
    *,
    mode: str,
    db_dir: Path,
    telemetryctl: Any | None = None,
) -> Any:
    return create_compress_adapter(
        mode=mode,
        db_path=db_dir,
        telemetryctl=telemetryctl,
    )


def create_skill_api(
    *,
    mode: str,
    db_dir: Path,
    home_root: Path,
    config: Any | None,
    telemetryctl: Any | None = None,
) -> Any:
    return create_skill_adapter(
        mode=mode,
        db_path=db_dir,
        home_root=home_root,
        config=config,
        telemetryctl=telemetryctl,
    )
