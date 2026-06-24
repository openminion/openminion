from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class OpenMinionRuntime:
    """Compatibility composition wrapper over the canonical API runtime.

    This wrapper exposes canonical runtime handles and marks unavailable
    module adapters as explicit optional fields (None) rather than fake wiring.
    """

    storage: object
    sess: object
    artifact: object | None
    mem: object | None
    skill: object | None
    agent_registry: object
    identity: object | None
    os: object
    llm: object
    ctx: object | None
    rlm: object | None
    meta: object
    brain: object
    runtime_manager: object

    @classmethod
    def from_config_path(cls, config_path: Optional[str] = None) -> "OpenMinionRuntime":
        from importlib import import_module
        from openminion.base.config import ConfigManager

        APIRuntime = import_module("openminion.api.runtime").APIRuntime
        bootstrap_config_manager = import_module(
            "openminion.services.bootstrap.config"
        ).bootstrap_config_manager

        manager = ConfigManager.load(config_path)
        bootstrap_config_manager(manager)
        cfg = manager.base_config
        api_runtime = APIRuntime.from_manager(manager)
        return cls(
            storage=api_runtime.storage_connection,
            sess=api_runtime.sessions,
            artifact=None,
            mem=getattr(api_runtime.gateway, "_agent_memory", None),
            skill=None,
            agent_registry=cfg.agents,
            identity=None,
            os=api_runtime.tools,
            llm=api_runtime.provider,
            ctx=getattr(api_runtime.gateway, "_session_context", None),
            rlm=None,
            meta=api_runtime.security_policy,
            brain=api_runtime.agent,
            runtime_manager=api_runtime,
        )

    @property
    def runtimectl(self) -> Any:
        return self.runtime_manager

    @property
    def tool(self) -> object:
        return self.os

    @property
    def sessctl(self) -> object:
        return self.sess

    @property
    def artifactctl(self) -> object:
        return self.artifact

    def close(self) -> None:
        self.runtime_manager.close()
