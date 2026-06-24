from collections.abc import Mapping

from openminion.base.config.env import EnvironmentConfig
from openminion.services.config import (
    normalize_memory_capsule_strategy,
    resolve_services_env,
)
from openminion.services.constants import MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN
from openminion.services.gateway.constants import (
    OPENMINION_MEMORY_CAPSULE_STRATEGY_ENV,
    OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED_ENV,
)


def _resolve_gateway_env(agent: object) -> EnvironmentConfig:
    config_manager = getattr(agent, "_config_manager", None)
    env = getattr(config_manager, "env", None)
    if isinstance(env, EnvironmentConfig):
        return env
    runtime_cfg = getattr(agent, "_config", None)
    runtime_env = getattr(getattr(runtime_cfg, "runtime", None), "env", {})
    if not isinstance(runtime_env, Mapping):
        runtime_env = {}
    return resolve_services_env(runtime_env=runtime_env)


def resolve_memory_capsule_strategy(agent: object) -> str:
    config_value = ""
    runtime_cfg = getattr(agent, "_config", None)
    if runtime_cfg is not None:
        config_value = str(
            getattr(
                getattr(runtime_cfg, "runtime", None), "memory_capsule_strategy", ""
            )
            or ""
        ).strip()
    env_value = str(
        _resolve_gateway_env(agent).get(OPENMINION_MEMORY_CAPSULE_STRATEGY_ENV, "")
        or ""
    ).strip()
    return normalize_memory_capsule_strategy(
        env_value or config_value or MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN
    )


def resolve_memory_dynamic_retrieval_enabled(agent: object) -> bool:
    config_value = False
    runtime_cfg = getattr(agent, "_config", None)
    if runtime_cfg is not None:
        config_value = bool(
            getattr(
                getattr(runtime_cfg, "runtime", None),
                "memory_dynamic_retrieval_enabled",
                False,
            )
        )
    env_owner = _resolve_gateway_env(agent)
    if env_owner.has(OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED_ENV):
        return env_owner.get_bool(
            OPENMINION_MEMORY_DYNAMIC_RETRIEVAL_ENABLED_ENV,
            default=config_value,
        )
    return config_value
