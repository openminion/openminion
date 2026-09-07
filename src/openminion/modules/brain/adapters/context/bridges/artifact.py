import logging
from typing import Any

from openminion.modules.artifact.models import sha_to_ref
from openminion.modules.context.schemas import ArtifactDigest

from .shared import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
)

logger = logging.getLogger(__name__)


class BridgeArtifactClient:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self,
        artifact_ctl: Any,
        *,
        owns_artifactctl: bool = False,
    ) -> None:
        self._artifact_ctl = artifact_ctl
        self._owns_artifactctl = owns_artifactctl

    def close(self) -> None:
        if self._owns_artifactctl:
            self._owns_artifactctl = False
            self._artifact_ctl.close()

    def query_digests(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> list[ArtifactDigest]:
        del agent_id
        try:
            results = self._artifact_ctl.search(
                query=query,
                filters={"owner_type": "session", "owner_id": session_id},
            )
            digests: list[ArtifactDigest] = []
            for meta in results[:limit]:
                view_ref = self._artifact_ctl.ensure_digest(meta.sha256)
                digest = self._artifact_ctl.read_digest(meta.sha256)
                digests.append(
                    ArtifactDigest(
                        ref=sha_to_ref(meta.sha256),
                        view_id=view_ref.ref,
                        digest_hash=view_ref.sha256,
                        excerpt=str(digest.get("excerpt") or "") or None,
                    )
                )
            return digests
        except Exception as exc:
            logger.warning("artifact query_digests failed: %s", exc)
            return []


__all__ = ["BridgeArtifactClient"]
