from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Protocol

from .errors import RetrieveCtlError


RETRIEVE_INTERFACE_VERSION = "v1"
RETRIEVE_STORAGE_INTERFACE_VERSION = "v1"


class RetrieveCtlInterface(Protocol):
    """RetrieveCtl interface contract."""

    contract_version: ClassVar[str] = RETRIEVE_INTERFACE_VERSION

    def __init__(
        self,
        config: str | Path | dict[str, Any] | Any = None,  # RetrieveCtlConfig
        vector_adapter: Any = None,
    ) -> None: ...

    def close(self) -> None: ...

    def retrieve(
        self,
        *,
        query: str,
        purpose: str,
        scope: dict[str, Any],
        k: int,
        strategy: str,
        filters: dict[str, Any] | Any | None = None,  # RetrievalFilters
    ) -> list[dict[str, Any]]: ...  # RetrievedItem

    def expand(
        self, *, ref: str, mode: str, k: int
    ) -> list[dict[str, Any]]: ...  # RetrievedItem

    def explain(
        self, item: dict[str, Any] | Any | str
    ) -> dict[str, Any]: ...  # RetrievedItem

    def ingest_artifact(
        self, artifact_ref: str, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...  # IngestResult

    def ingest_skill(
        self,
        skill_id: str,
        version_hash: str,
        source_ref: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...  # IngestResult

    def ingest_memory(
        self, mem_id: str, text: str, meta: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...  # IngestResult

    def ingest_source(
        self,
        *,
        source_type: str,
        source_ref: str,
        text: str,
        scope: str,
        tags: list[str] | None = None,
        title: str | None = None,
        corpus_id: str | None = None,
        unit_kind: str | None = None,
        created_at: str | None = None,
    ) -> Any: ...  # IngestResult

    def ingest_event(
        self, event_type: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None: ...

    def build_raptor_tree(self, doc_id: str) -> dict[str, Any]: ...

    def group_long_units(
        self, corpus_id: str, grouping_policy: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


class RetrieveStorageInterface(Protocol):
    """Retrieve storage contract used by RetrieveCtl runtime/service layer."""

    contract_version: ClassVar[str] = RETRIEVE_STORAGE_INTERFACE_VERSION

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any: ...

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> Any | None: ...

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]: ...

    def commit(self) -> None: ...


def ensure_retrieve_compatibility(
    retrieve_ctl: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate retrieve control implements the required interface."""
    errors: list[str] = []

    # Check contract version
    if not hasattr(retrieve_ctl, "contract_version"):
        errors.append("Missing contract_version attribute")
    elif retrieve_ctl.contract_version != RETRIEVE_INTERFACE_VERSION:
        errors.append(
            f"Version mismatch: expected {RETRIEVE_INTERFACE_VERSION}, "
            f"got {retrieve_ctl.contract_version}"
        )

    # Check required methods
    required_methods = [
        "close",
        "retrieve",
        "expand",
        "explain",
        "ingest_artifact",
        "ingest_skill",
        "ingest_memory",
        "ingest_source",
        "ingest_event",
        "build_raptor_tree",
        "group_long_units",
    ]

    for method in required_methods:
        if not hasattr(retrieve_ctl, method) or not callable(
            getattr(retrieve_ctl, method)
        ):
            errors.append(f"Missing required method: {method}")

    if errors:
        if strict:
            raise RetrieveCtlError(
                "RETRIEVE_CTL_INTERFACE_VIOLATION",
                f"Retrieve controller incompatible: {errors}",
            )
        return False, errors

    return True, []


def ensure_retrieve_storage_compatibility(
    storage: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    """Validate retrieve storage adapter contract."""
    errors: list[str] = []

    required_members = ("contract_version", "execute", "fetchone", "fetchall", "commit")
    for member in required_members:
        if not hasattr(storage, member):
            errors.append(f"Missing required member: {member}")
            continue
        if member == "contract_version":
            continue
        if not callable(getattr(storage, member)):
            errors.append(f"Member is not callable: {member}")

    version = str(getattr(storage, "contract_version", "")).strip()
    if version != RETRIEVE_STORAGE_INTERFACE_VERSION:
        errors.append(
            "Version mismatch: expected "
            f"{RETRIEVE_STORAGE_INTERFACE_VERSION}, got {version or '<missing>'}"
        )

    if errors:
        if strict:
            raise TypeError("Retrieve storage incompatible: " + "; ".join(errors))
        return False, errors
    return True, []
