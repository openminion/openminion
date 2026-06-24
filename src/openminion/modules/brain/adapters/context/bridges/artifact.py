"""Modules brain adapters context bridges artifact."""

import logging
from typing import Any

from openminion.modules.context.schemas import ArtifactDigest

from .shared import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    _extract_text_from_record,
    _lazy_resolve_service,
    _resolve_database_path,
)

logger = logging.getLogger(__name__)


class BridgeArtifactClient:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, backing_store: Any) -> None:
        self._store = backing_store
        self._artifact_ctl: Any | None = None

    def _resolve_artifactctl(self) -> Any | None:
        return _lazy_resolve_service(
            self,
            cache_attr="_artifact_ctl",
            import_loader=_import_artifact_dependencies,
            factory=self._build_artifact_ctl,
        )

    def _build_artifact_ctl(self, imported: tuple[Any, Any]) -> Any | None:
        artifact_ctl_cls, sqlite_artifact_store_cls = imported
        db_path = _resolve_database_path(self._store)
        if db_path is None:
            return None
        artifact_db = db_path.parent / "artifact.db"
        return artifact_ctl_cls(store=sqlite_artifact_store_cls(artifact_db))

    def query_digests(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> list[ArtifactDigest]:
        del agent_id
        artifact_ctl = self._resolve_artifactctl()
        if artifact_ctl is None:
            logger.debug("artifact infrastructure not available")
            return []
        try:
            results = artifact_ctl.search(
                query=query,
                filters={"owner_type": "session", "owner_id": session_id},
            )
            return (
                [
                    ArtifactDigest(
                        ref=_extract_text_from_record(
                            meta, attr_keys=("sha256", "ref")
                        ),
                        view_id=getattr(meta, "view_id", None),
                        digest_hash=_extract_text_from_record(
                            meta, attr_keys=("sha256", "digest_hash")
                        ),
                    )
                    for meta in results[:limit]
                ]
                if results
                else []
            )
        except Exception as exc:
            logger.warning("artifact query_digests failed: %s", exc)
            return []


def _import_artifact_dependencies() -> tuple[Any, Any] | None:
    try:
        from openminion.modules.artifact.control import ArtifactCtl
        from openminion.modules.artifact.storage.store import SQLiteArtifactStore
    except Exception:
        return None
    return ArtifactCtl, SQLiteArtifactStore


__all__ = ["BridgeArtifactClient"]
