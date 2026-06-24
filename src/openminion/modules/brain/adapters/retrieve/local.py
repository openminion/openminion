import logging
from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION

_LOGGER = logging.getLogger(__name__)


class LocalRetrieveAdapter:
    """Local fallback adapter for retrieval operations."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self) -> None:
        _LOGGER.info(
            "LocalRetrieveAdapter instantiated - retrieval returns empty results"
        )

    def retrieve(self, query: str, *, top_k: int = 10, **kwargs: Any) -> list[dict]:
        _LOGGER.debug(
            "LocalRetrieveAdapter.retrieve() called - returning empty results"
        )
        del query, top_k, kwargs
        return []

    def retrieve_with_context(
        self, query: str, context: dict[str, Any], *, top_k: int = 10, **kwargs: Any
    ) -> list[dict]:
        del query, context, top_k, kwargs
        return []

    def ingest_skill(
        self,
        *,
        skill_id: str,
        version_hash: str,
        source_ref: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del skill_id, version_hash, source_ref, meta
        return {}
