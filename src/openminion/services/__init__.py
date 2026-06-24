from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time contract only
    from openminion.services.agent import AgentService
    from openminion.services.gateway import GatewayService

__all__ = ["AgentService", "GatewayService"]


def __getattr__(name: str) -> Any:
    if name == "AgentService":
        from openminion.services.agent import AgentService

        return AgentService
    if name == "GatewayService":
        from openminion.services.gateway import GatewayService

        return GatewayService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
