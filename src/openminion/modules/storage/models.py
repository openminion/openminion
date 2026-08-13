from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BlobRef:
    algo: str
    hash: str
    path: str
    size: int
    media_type: str
    created_at: str
    ext: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "algo": self.algo,
            "hash": self.hash,
            "path": self.path,
            "size": int(self.size),
            "media_type": self.media_type,
            "created_at": self.created_at,
        }
        if self.ext:
            payload["ext"] = self.ext
        return payload


@dataclass(frozen=True)
class EventRef:
    event_id: str
    session_id: str | None
    persisted: str
    ts: str
    sidecar_path: str | None = None
    namespace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "persisted": self.persisted,
            "ts": self.ts,
        }
        if self.sidecar_path is not None:
            payload["sidecar_path"] = self.sidecar_path
        if self.namespace is not None:
            payload["namespace"] = self.namespace
        return payload


@dataclass(frozen=True)
class RowRef:
    table: str
    row_id: str
    persisted: str
    ts: str
    sidecar_path: str | None = None
    namespace: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "table": self.table,
            "row_id": self.row_id,
            "persisted": self.persisted,
            "ts": self.ts,
        }
        if self.sidecar_path is not None:
            payload["sidecar_path"] = self.sidecar_path
        if self.namespace is not None:
            payload["namespace"] = self.namespace
        return payload


@dataclass
class ReindexReport:
    scanned_files: int = 0
    scanned_lines: int = 0
    inserted: int = 0
    duplicates: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
    dry_run: bool = False
    file_reports: list[dict[str, Any]] = field(default_factory=list)
    archived_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
