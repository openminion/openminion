from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

from openminion.modules.artifact.models import AliasRecord, ArtifactMeta, ViewRecord

ArtifactFilters = dict[str, object] | None


class BlobStore(ABC):
    @abstractmethod
    def put_bytes(self, sha256: str, data: bytes) -> None:
        raise NotImplementedError

    @abstractmethod
    def put_file(self, sha256: str, path: str | Path) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_stream(self, sha256: str) -> BinaryIO:
        raise NotImplementedError

    @abstractmethod
    def exists(self, sha256: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete(self, sha256: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def path_for(self, sha256: str) -> str:
        raise NotImplementedError


class ArtifactIndex(ABC):
    @abstractmethod
    def upsert_artifact(self, meta: ArtifactMeta) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_artifact(
        self, sha256: str, *, include_deleted: bool = True
    ) -> ArtifactMeta | None:
        raise NotImplementedError

    @abstractmethod
    def list_recent(
        self, limit: int = 50, filters: ArtifactFilters = None
    ) -> list[ArtifactMeta]:
        raise NotImplementedError

    @abstractmethod
    def search(
        self, query: str, filters: ArtifactFilters = None, limit: int = 100
    ) -> list[ArtifactMeta]:
        raise NotImplementedError

    @abstractmethod
    def largest(
        self, limit: int = 50, filters: ArtifactFilters = None
    ) -> list[ArtifactMeta]:
        raise NotImplementedError

    @abstractmethod
    def upsert_view(self, view: ViewRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_view(
        self,
        raw_sha256: str,
        view_type: str,
        schema_version: str,
        policy_hash: str = "",
        *,
        include_deleted: bool = True,
    ) -> ViewRecord | None:
        raise NotImplementedError

    @abstractmethod
    def list_views(
        self, raw_sha256: str, *, include_deleted: bool = False
    ) -> list[ViewRecord]:
        raise NotImplementedError

    @abstractmethod
    def alias_set(
        self,
        alias: str,
        sha256: str,
        *,
        expires_at: str | None = None,
        meta_json: dict[str, object] | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def alias_resolve(self, alias: str) -> AliasRecord | None:
        raise NotImplementedError

    @abstractmethod
    def alias_list(self, prefix: str | None = None) -> list[AliasRecord]:
        raise NotImplementedError

    @abstractmethod
    def alias_delete(self, alias: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_reference(self, owner_type: str, owner_id: str, sha256: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def remove_reference(self, owner_type: str, owner_id: str, sha256: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def active_reference_shas(self) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    def active_alias_shas(self) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    def recent_artifact_shas(self, keep_days: int) -> set[str]:
        raise NotImplementedError

    @abstractmethod
    def eligible_for_gc(self, older_than_days: int, protected: set[str]) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def soft_delete_artifacts(self, shas: Iterable[str], deleted_at: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def soft_delete_views_for_raw(self, raw_sha256: str, deleted_at: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def hard_delete_artifact(self, sha256: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def hard_delete_views_for_raw(self, raw_sha256: str) -> int:
        raise NotImplementedError

    @abstractmethod
    def purgeable_views(self, grace_days: int) -> list[ViewRecord]:
        raise NotImplementedError

    @abstractmethod
    def purgeable_artifacts(self, grace_days: int) -> list[ArtifactMeta]:
        raise NotImplementedError

    @abstractmethod
    def all_artifacts(self, *, include_deleted: bool = False) -> list[ArtifactMeta]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
