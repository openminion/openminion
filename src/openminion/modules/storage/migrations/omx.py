from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OmxResumeChunk:
    chunk_index: int
    row_start: int
    row_end: int
    sha256: str


@dataclass(frozen=True)
class OmxTableEntry:
    name: str
    path: str
    codec: str
    row_count: int
    sha256: str
    resume_chunks: list[OmxResumeChunk] = field(default_factory=list)


@dataclass(frozen=True)
class OmxSource:
    db_path: str
    user_version: int
    schema_head: str | None = None
    export_notes: str | None = None


@dataclass(frozen=True)
class OmxManifest:
    format: str
    format_version: str
    module_id: str
    module_application_id: int
    created_at: str
    source: OmxSource
    tables: list[OmxTableEntry]
    blobs: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source"] = asdict(self.source)
        payload["tables"] = []
        for table in self.tables:
            table_payload = asdict(table)
            table_payload["resume_chunks"] = [
                asdict(chunk) for chunk in table.resume_chunks
            ]
            payload["tables"].append(table_payload)
        return payload


def dump_manifest(manifest: OmxManifest, path: str | Path) -> None:
    path = Path(path).expanduser().resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.to_dict(), indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def load_manifest(path: str | Path) -> OmxManifest:
    path = Path(path).expanduser().resolve(strict=False)
    payload = json.loads(path.read_text(encoding="utf-8"))

    source_payload = payload["source"]
    source = OmxSource(
        db_path=str(source_payload["db_path"]),
        user_version=int(source_payload["user_version"]),
        schema_head=source_payload.get("schema_head"),
        export_notes=source_payload.get("export_notes"),
    )

    table_entries: list[OmxTableEntry] = []
    for item in payload.get("tables", []):
        chunks = [
            OmxResumeChunk(
                chunk_index=int(chunk["chunk_index"]),
                row_start=int(chunk["row_start"]),
                row_end=int(chunk["row_end"]),
                sha256=str(chunk["sha256"]),
            )
            for chunk in item.get("resume_chunks", [])
        ]
        table_entries.append(
            OmxTableEntry(
                name=str(item["name"]),
                path=str(item["path"]),
                codec=str(item["codec"]),
                row_count=int(item["row_count"]),
                sha256=str(item["sha256"]),
                resume_chunks=chunks,
            )
        )

    return OmxManifest(
        format=str(payload["format"]),
        format_version=str(payload["format_version"]),
        module_id=str(payload["module_id"]),
        module_application_id=int(payload["module_application_id"]),
        created_at=str(payload["created_at"]),
        source=source,
        tables=table_entries,
        blobs=payload.get("blobs"),
    )
