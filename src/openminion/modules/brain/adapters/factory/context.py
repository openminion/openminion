from typing import Any

from .modes import mode_is_local, raise_if_strict
from .environment import context_feature_flags, env_bool


def create_context_adapter(
    mode: str = "auto",
    session_store: Any = None,
    *,
    identity_system_prompt: str | None = None,
    identity_budget_config: Any | None = None,
    runtime_token_budget: int | None = None,
    rlmctl: Any = None,
    vectorctl: Any = None,
    vector_adapter: Any = None,
    telemetryctl: Any | None = None,
    skill_config: Any | None = None,
    skill_home_root: Any | None = None,
) -> Any:
    from openminion.modules.brain.adapters.context import LocalContextAdapter

    if mode_is_local(mode):
        return LocalContextAdapter(session_store=session_store)
    try:
        from openminion.modules.context.service import ContextCtlService
        from openminion.modules.brain.adapters.context import ContextCtlAdapter
        from openminion.modules.brain.adapters.context.bridges import (
            BridgeIdentityClient,
            BridgeSessionClient,
            BridgeMemoryClient,
            BridgeArtifactClient,
            BridgeSkillClient,
            BridgeCompressClient,
        )

        feature_flags = context_feature_flags(env_bool=env_bool)

        service = ContextCtlService(
            identityctl=BridgeIdentityClient(
                backing_store=session_store,
                system_prompt=identity_system_prompt,
            ),
            sessctl=BridgeSessionClient(backing_store=session_store),
            memctl=BridgeMemoryClient(backing_store=session_store),
            artifactctl=BridgeArtifactClient(backing_store=session_store),
            skillctl=BridgeSkillClient(
                backing_store=session_store,
                skill_config=skill_config,
                skill_home_root=skill_home_root,
            ),
            compressctl=BridgeCompressClient(backing_store=session_store),
            rlmctl=rlmctl,
            vectorctl=vectorctl,
            vector_adapter=vector_adapter,
            telemetryctl=telemetryctl,
            identity_budget=identity_budget_config,
            rolling_enabled=feature_flags["rolling_enabled"],
            compaction_enabled=feature_flags["compaction_enabled"],
            compression_enabled=feature_flags["compression_enabled"],
        )
        return ContextCtlAdapter(
            service=service,
            runtime_token_budget=runtime_token_budget,
        )
    except ImportError:
        raise_if_strict(mode)
        return LocalContextAdapter(session_store=session_store)
