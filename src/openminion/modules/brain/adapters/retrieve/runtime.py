from typing import Any

from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl


class RetrievectlAdapter:
    """Adapter for retrieval operations wrapping RetrieveCtl."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, service: RetrieveCtl) -> None:
        self._svc = service

    def set_telemetry_context(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> None:
        setter = getattr(self._svc, "set_telemetry_context", None)
        if callable(setter):
            setter(session_id=session_id, turn_id=turn_id)

    def retrieve(self, query: str, *, top_k: int = 10, **kwargs) -> list[dict]:
        return self._svc.retrieve(query=query, k=kwargs.pop("k", top_k), **kwargs)

    def retrieve_with_context(
        self, query: str, context: dict[str, Any], *, top_k: int = 10, **kwargs
    ) -> list[dict]:
        return self._svc.retrieve_with_context(
            query,
            context,
            top_k=kwargs.pop("k", top_k),
            **kwargs,
        )

    def ingest_skill(
        self,
        *,
        skill_id: str,
        version_hash: str,
        source_ref: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._svc.ingest_skill(
            skill_id=skill_id,
            version_hash=version_hash,
            source_ref=source_ref,
            meta=meta,
        )

    def get_retrieval_stats(
        self,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        from openminion.modules.memory.diagnostics.introspection import (
            build_retrieval_stats,
        )

        return build_retrieval_stats(
            retrieve_svc=self._svc, session_id=session_id
        ).model_dump(mode="json")
