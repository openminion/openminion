from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from openminion.modules.artifact.config import ArtifactCtlConfig
    from openminion.modules.artifact.models import ArtifactMeta, ArtifactRef, ViewRecord
    from typing import BinaryIO


ARTIFACT_INTERFACE_VERSION = "v1"


class ArtifactCtlInterface(Protocol):
    """Artifact Control interface contract."""

    contract_version: ClassVar[str] = ARTIFACT_INTERFACE_VERSION

    def __init__(self, config: str | ArtifactCtlConfig) -> None: ...

    def ingest_bytes(
        self,
        data: bytes,
        mime: str | None = None,
        original_name: str | None = None,
        label: str | None = None,
        meta: dict[str, Any] | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        retrieve_ctl: Any | None = None,
    ) -> ArtifactRef: ...

    def ingest_file(
        self,
        path: str,
        mime: str | None = None,
        label: str | None = None,
        meta: dict[str, Any] | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        retrieve_ctl: Any | None = None,
    ) -> ArtifactRef: ...

    def get(self, ref_or_sha: str) -> ArtifactMeta: ...

    def open(self, ref_or_sha: str) -> BinaryIO: ...

    def read_bytes(self, ref_or_sha: str) -> bytes: ...

    def ensure_digest(
        self, ref_or_sha: str, policy: dict[str, Any] | None = None
    ) -> ArtifactRef: ...

    def ensure_view(
        self, ref_or_sha: str, view_type: str, policy: dict[str, Any] | None = None
    ) -> ArtifactRef: ...

    def list_views(self, ref_or_sha: str) -> list[ViewRecord]: ...

    def read_digest(self, ref_or_sha: str) -> dict[str, Any]: ...

    def read_view(self, ref_or_sha: str, view_type: str) -> Any: ...

    def alias_set(
        self,
        alias: str,
        ref_or_sha: str,
        overwrite: bool = True,
        expires_at: str | None = None,
        meta_json: dict[str, Any] | None = None,
    ) -> None: ...

    def alias_resolve(self, alias: str) -> ArtifactRef | None: ...

    def alias_list(self, prefix: str | None = None) -> list[dict[str, Any]]: ...

    def alias_delete(self, alias: str) -> None: ...

    def list_recent(
        self, limit: int = 50, scope_filters: dict[str, Any] | None = None
    ) -> list[ArtifactMeta]: ...

    def search(
        self, query: str, filters: dict[str, Any] | None = None
    ) -> list[ArtifactMeta]: ...

    def largest(
        self, limit: int = 50, filters: dict[str, Any] | None = None
    ) -> list[ArtifactMeta]: ...

    def gc(
        self,
        plan_only: bool = False,
        *,
        keep_days: int | None = None,
        delete_unreferenced_after_days: int | None = None,
    ) -> Any: ...  # GCReport type

    def delete(self, ref_or_sha: str, soft: bool = True) -> None: ...

    def purge(self, grace_days: int | None = None) -> Any: ...  # PurgeReport type

    def verify(self, target: str = "all") -> Any: ...  # VerifyReport type

    def ref_add(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None: ...

    def ref_remove(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None: ...

    def close(self) -> None: ...


def ensure_artifact_compatibility(
    ctl: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate artifact controller implements the required interface."""
    errors = []

    if not hasattr(ctl, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif ctl.contract_version != ARTIFACT_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {ARTIFACT_INTERFACE_VERSION}, "
            f"got {ctl.contract_version}"
        )

    for method in _REQUIRED_METHODS:
        if not hasattr(ctl, method) or not callable(getattr(ctl, method)):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:
            from openminion.modules.artifact.errors import ArtifactCtlError

            raise ArtifactCtlError(
                "ARTIFACT_CTL_INTERFACE_VIOLATION",
                f"Artifact controller incompatible: {errors}",
            )
        return False, errors

    return True, []


_REQUIRED_METHODS = (
    "ingest_bytes",
    "ingest_file",
    "get",
    "open",
    "read_bytes",
    "ensure_digest",
    "ensure_view",
    "list_views",
    "read_digest",
    "read_view",
    "alias_set",
    "alias_resolve",
    "alias_list",
    "alias_delete",
    "list_recent",
    "search",
    "largest",
    "gc",
    "delete",
    "purge",
    "verify",
    "ref_add",
    "ref_remove",
    "close",
)
