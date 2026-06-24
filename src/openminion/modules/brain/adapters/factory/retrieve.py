from typing import Any

from .modes import use_local_when_service_missing


def create_retrieve_adapter(
    mode: str = "auto",
    service: Any = None,
    *,
    telemetryctl: Any | None = None,
) -> Any:
    """RIG-01: Create retrieve adapter for RLM RAG integration."""
    del telemetryctl
    if use_local_when_service_missing(mode, service):
        from openminion.modules.brain.adapters.retrieve import LocalRetrieveAdapter

        return LocalRetrieveAdapter()
    from ..retrieve import RetrievectlAdapter

    return RetrievectlAdapter(service)
