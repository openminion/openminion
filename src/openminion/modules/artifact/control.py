"""Artifact control surface for ingest, lookup, verification, and retention."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import mimetypes
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from openminion.base.time import utc_now_iso as iso_now
from openminion.modules.artifact.config import ArtifactCtlConfig, load_config
from openminion.modules.artifact.constants import (
    DEFAULT_CONFIG_FILENAME,
    VALID_OWNER_TYPES,
)
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.interfaces import ARTIFACT_INTERFACE_VERSION
from openminion.modules.artifact.models import (
    ArtifactMeta,
    ArtifactRef,
    GCReport,
    PurgeReport,
    VerifyIssue,
    VerifyReport,
    ViewRecord,
    parse_ref_or_sha,
)
from openminion.modules.artifact.storage import (
    ArtifactIndex,
    BlobStore,
    FileSystemCASBlobStore,
    SQLiteArtifactIndex,
)

_SCHEMA_VERSION = "v1"
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_NUM_RE = re.compile(r"(?<![0-9A-Fa-f])\d{12,19}(?![0-9A-Fa-f])")
_ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}-?\d{2}-?\d{2}T\d{2,14}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_FALLBACK_MIME_BY_SUFFIX = {
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
}
logger = logging.getLogger(__name__)


class ArtifactCtl:
    contract_version = ARTIFACT_INTERFACE_VERSION

    def __init__(
        self,
        config: str
        | Path
        | dict[str, Any]
        | ArtifactCtlConfig = DEFAULT_CONFIG_FILENAME,
    ) -> None:
        self.config = load_config(config)

        blob_backend = self.config.blob_store.backend.lower()
        if blob_backend != "filesystem_cas":
            raise ArtifactCtlError(
                "INVALID_CONFIG",
                f"Unsupported blob backend: {self.config.blob_store.backend}",
            )

        index_backend = self.config.index.backend.lower()
        if index_backend != "sqlite":
            raise ArtifactCtlError(
                "INVALID_CONFIG",
                f"Unsupported index backend: {self.config.index.backend}",
            )

        self.blob_store: BlobStore = FileSystemCASBlobStore(
            self.config.blob_store.root_dir
        )
        self.index: ArtifactIndex = SQLiteArtifactIndex(
            sqlite_path=self.config.index.sqlite_path,
            wal=self.config.index.wal,
        )

    def __enter__(self) -> ArtifactCtl:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

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
    ) -> ArtifactRef:
        return self._ingest_bytes_internal(
            data=data,
            mime=mime,
            original_name=original_name,
            original_path=original_name,
            label=label,
            meta=meta,
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            auto_generate=True,
            retrieve_ctl=retrieve_ctl,
        )

    def ingest_file(
        self,
        path: str | Path,
        mime: str | None = None,
        label: str | None = None,
        meta: dict[str, Any] | None = None,
        session_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
        retrieve_ctl: Any | None = None,
    ) -> ArtifactRef:
        src = Path(path).expanduser().resolve(strict=True)
        self._enforce_ingest_size(src.stat().st_size, source=str(src))
        sha256, size_bytes = _hash_file(src)
        sniff = _read_sample(src)
        inferred_mime = _determine_mime(provided=mime, path=src, sample=sniff)
        encoding = _detect_encoding(sniff)

        if not self.blob_store.exists(sha256):
            self.blob_store.put_file(sha256, src)

        created_at = iso_now()
        original_name = src.name
        original_path = str(src) if self.config.security.store_original_path else None
        artifact_meta = ArtifactMeta(
            sha256=sha256,
            size_bytes=size_bytes,
            mime=inferred_mime,
            created_at=created_at,
            original_name=original_name,
            original_path=original_path,
            label=label,
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            encoding=encoding,
            meta_json=meta,
        )
        self.index.upsert_artifact(artifact_meta)

        self._auto_generate_views(sha256)
        ref = artifact_meta.to_ref()
        self._emit_retrieve_ingest_event(
            retrieve_ctl=retrieve_ctl,
            artifact_ref=ref,
            artifact_text=_extract_ingest_text_from_file(src),
            meta=meta,
            fallback_title=label or src.name,
        )
        return ref

    def get(self, ref_or_sha: str) -> ArtifactMeta:
        sha = self._resolve_sha(ref_or_sha)
        meta = self.index.get_artifact(sha, include_deleted=True)
        if meta is None:
            raise ArtifactCtlError("NOT_FOUND", f"Artifact not found: {ref_or_sha}")
        return meta

    def open(self, ref_or_sha: str) -> BinaryIO:
        sha = self._resolve_sha(ref_or_sha)
        if not self.blob_store.exists(sha):
            raise ArtifactCtlError("NOT_FOUND", f"Blob is missing for artifact: {sha}")
        return self.blob_store.get_stream(sha)

    def read_bytes(self, ref_or_sha: str) -> bytes:
        with self.open(ref_or_sha) as fh:
            return fh.read()

    def ensure_digest(
        self, ref_or_sha: str, policy: dict[str, Any] | None = None
    ) -> ArtifactRef:
        return self.ensure_view(ref_or_sha, "digest", policy=policy)

    def ensure_view(
        self, ref_or_sha: str, view_type: str, policy: dict[str, Any] | None = None
    ) -> ArtifactRef:
        normalized = (view_type or "").strip().lower()
        if normalized not in {"digest", "text", "json", "table"}:
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", f"Unsupported view type: {view_type}"
            )

        raw_sha = self._resolve_sha(ref_or_sha)
        raw_meta = self.index.get_artifact(raw_sha, include_deleted=False)
        if raw_meta is None:
            raise ArtifactCtlError("NOT_FOUND", f"Artifact not found: {ref_or_sha}")

        policy_hash = _policy_hash(policy)
        existing = self.index.get_view(
            raw_sha, normalized, _SCHEMA_VERSION, policy_hash, include_deleted=False
        )
        if existing and existing.view_sha256:
            view_meta = self.index.get_artifact(
                existing.view_sha256, include_deleted=False
            )
            if view_meta and self.blob_store.exists(existing.view_sha256):
                return view_meta.to_ref()

        payload, mime = self._generate_view_payload(raw_meta, normalized)
        view_name = (
            f"{raw_sha}.{normalized}.json"
            if mime == "application/json"
            else f"{raw_sha}.{normalized}.txt"
        )
        view_ref = self._ingest_bytes_internal(
            data=payload,
            mime=mime,
            original_name=view_name,
            original_path=view_name,
            label=f"view:{normalized}:{raw_sha[:12]}",
            meta={
                "derived_from": raw_sha,
                "view_type": normalized,
                "schema_version": _SCHEMA_VERSION,
            },
            session_id=raw_meta.session_id,
            trace_id=raw_meta.trace_id,
            agent_id=raw_meta.agent_id,
            auto_generate=False,
        )

        view_record = ViewRecord(
            raw_sha256=raw_sha,
            view_type=normalized,
            schema_version=_SCHEMA_VERSION,
            policy_hash=policy_hash,
            view_sha256=view_ref.sha256,
            view_path=None,
            mime=mime,
            size_bytes=view_ref.size_bytes,
            created_at=view_ref.created_at,
        )
        self.index.upsert_view(view_record)
        return view_ref

    def list_views(self, ref_or_sha: str) -> list[ViewRecord]:
        raw_sha = self._resolve_sha(ref_or_sha)
        return self.index.list_views(raw_sha)

    def read_digest(self, ref_or_sha: str) -> dict[str, Any]:
        payload = self.read_view(ref_or_sha, "digest")
        if isinstance(payload, dict):
            return payload
        raise ArtifactCtlError("INTERNAL_ERROR", "Digest payload is not JSON")

    def read_view(self, ref_or_sha: str, view_type: str) -> Any:
        view_ref = self.ensure_view(ref_or_sha, view_type)
        raw = self.read_bytes(view_ref.sha256)
        if view_ref.mime == "application/json":
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception as exc:
                raise ArtifactCtlError(
                    "INTERNAL_ERROR", f"Failed to decode JSON view: {exc}"
                ) from exc
        return raw.decode("utf-8", errors="replace")

    def alias_set(
        self,
        alias: str,
        ref_or_sha: str,
        overwrite: bool = True,
        expires_at: str | None = None,
        meta_json: dict[str, Any] | None = None,
    ) -> None:
        normalized_alias = (alias or "").strip()
        if not normalized_alias:
            raise ArtifactCtlError("INVALID_ARGUMENT", "Alias cannot be empty")

        existing = self.index.alias_resolve(normalized_alias)
        if existing and not overwrite:
            raise ArtifactCtlError("ALREADY_EXISTS", f"Alias already exists: {alias}")

        sha = self._resolve_sha(ref_or_sha)
        if expires_at is None and self.config.aliases.expire_default_days > 0:
            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(days=self.config.aliases.expire_default_days)
            ).isoformat()
        self.index.alias_set(
            normalized_alias, sha, expires_at=expires_at, meta_json=meta_json
        )

    def alias_resolve(self, alias: str) -> ArtifactRef | None:
        rec = self.index.alias_resolve(alias)
        if rec is None:
            return None
        meta = self.index.get_artifact(rec.sha256, include_deleted=False)
        if meta is None:
            return None
        return meta.to_ref()

    def alias_list(self, prefix: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "alias": row.alias,
                "ref": f"artifact://sha256/{row.sha256}",
                "sha256": row.sha256,
                "updated_at": row.updated_at,
                "expires_at": row.expires_at,
                "meta": row.meta_json,
            }
            for row in self.index.alias_list(prefix=prefix)
        ]

    def alias_delete(self, alias: str) -> None:
        self.index.alias_delete(alias)

    def list_recent(
        self, limit: int = 50, scope_filters: dict[str, Any] | None = None
    ) -> list[ArtifactMeta]:
        return self.index.list_recent(limit=limit, filters=scope_filters)

    def search(
        self, query: str, filters: dict[str, Any] | None = None
    ) -> list[ArtifactMeta]:
        return self.index.search(query=query, filters=filters)

    def largest(
        self, limit: int = 50, filters: dict[str, Any] | None = None
    ) -> list[ArtifactMeta]:
        return self.index.largest(limit=limit, filters=filters)

    def gc(
        self,
        plan_only: bool = False,
        *,
        keep_days: int | None = None,
        delete_unreferenced_after_days: int | None = None,
    ) -> GCReport:
        keep = self.config.retention.keep_days if keep_days is None else int(keep_days)
        delete_after = (
            self.config.retention.delete_unreferenced_after_days
            if delete_unreferenced_after_days is None
            else int(delete_unreferenced_after_days)
        )

        root_set = set()
        root_set |= self.index.active_reference_shas()
        root_set |= self.index.active_alias_shas()

        protected = set(root_set)
        protected |= self.index.recent_artifact_shas(keep)

        eligible = self.index.eligible_for_gc(delete_after, protected)
        marked = 0
        if not plan_only and eligible:
            now = iso_now()
            marked = self.index.soft_delete_artifacts(eligible, now)
            for sha in eligible:
                self.index.soft_delete_views_for_raw(sha, now)

        return GCReport(
            keep_days=keep,
            delete_unreferenced_after_days=delete_after,
            roots=len(root_set),
            protected=len(protected),
            eligible=len(eligible),
            marked_deleted=marked,
            candidates=eligible,
        )

    def delete(self, ref_or_sha: str, soft: bool = True) -> None:
        sha = self._resolve_sha(ref_or_sha)
        now = iso_now()
        self.index.soft_delete_artifacts([sha], now)
        self.index.soft_delete_views_for_raw(sha, now)
        if not soft:
            self.blob_store.delete(sha)
            for view in self.index.list_views(sha, include_deleted=True):
                if view.view_sha256:
                    self.blob_store.delete(view.view_sha256)

    def purge(self, grace_days: int | None = None) -> PurgeReport:
        grace = (
            self.config.retention.purge_grace_days
            if grace_days is None
            else int(grace_days)
        )
        missing = 0
        views = self.index.purgeable_views(grace)
        artifacts = self.index.purgeable_artifacts(grace)
        handled_blob_shas: set[str] = set()

        purged_blobs = 0
        purged_views = 0
        delete_views_first = bool(self.config.retention.delete_views_first)

        def _purge_blob_sha(sha256: str) -> str:
            if sha256 in handled_blob_shas:
                return "handled"
            handled_blob_shas.add(sha256)
            if self.blob_store.exists(sha256):
                self.blob_store.delete(sha256)
                return "deleted"
            return "missing"

        def _purge_views() -> None:
            nonlocal missing, purged_views
            for view in views:
                if view.view_sha256:
                    status = _purge_blob_sha(view.view_sha256)
                    if status in {"deleted", "handled"}:
                        purged_views += 1
                    else:
                        missing += 1

        def _purge_artifacts() -> None:
            nonlocal missing, purged_blobs
            for artifact in artifacts:
                status = _purge_blob_sha(artifact.sha256)
                if status in {"deleted", "handled"}:
                    purged_blobs += 1
                else:
                    missing += 1

        if delete_views_first:
            _purge_views()
            _purge_artifacts()
        else:
            _purge_artifacts()
            _purge_views()

        for artifact in artifacts:
            self.index.hard_delete_views_for_raw(artifact.sha256)
            self.index.hard_delete_artifact(artifact.sha256)

        return PurgeReport(
            grace_days=grace,
            purged_views=purged_views,
            purged_blobs=purged_blobs,
            missing_files=missing,
        )

    def verify(self, target: str = "all") -> VerifyReport:
        if (target or "all") == "all":
            rows = self.index.all_artifacts(include_deleted=False)
        else:
            rows = [self.get(target)]

        checked = 0
        ok = 0
        issues: list[VerifyIssue] = []

        for row in rows:
            checked += 1
            if not self.blob_store.exists(row.sha256):
                issues.append(VerifyIssue(sha256=row.sha256, issue="missing_blob"))
                continue

            actual = _sha256_path(Path(self.blob_store.path_for(row.sha256)))
            if actual != row.sha256:
                issues.append(
                    VerifyIssue(
                        sha256=row.sha256,
                        issue="digest_mismatch",
                        expected_sha256=row.sha256,
                        actual_sha256=actual,
                    )
                )
                continue
            ok += 1

        return VerifyReport(checked=checked, ok=ok, failed=len(issues), issues=issues)

    def ref_add(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self._validate_owner_type(owner_type)
        sha = self._resolve_sha(ref_or_sha)
        self.index.add_reference(owner_type, owner_id, sha)

    def ref_remove(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self._validate_owner_type(owner_type)
        sha = self._resolve_sha(ref_or_sha)
        self.index.remove_reference(owner_type, owner_id, sha)

    def close(self) -> None:
        self.index.close()

    def _ingest_bytes_internal(
        self,
        *,
        data: bytes,
        mime: str | None,
        original_name: str | None,
        original_path: str | None,
        label: str | None,
        meta: dict[str, Any] | None,
        session_id: str | None,
        trace_id: str | None,
        agent_id: str | None,
        auto_generate: bool,
        retrieve_ctl: Any | None = None,
    ) -> ArtifactRef:
        self._enforce_ingest_size(len(data), source=original_name or "bytes")
        sha256 = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)
        inferred_mime = _determine_mime(
            provided=mime,
            path=Path(original_name) if original_name else None,
            sample=data[:4096],
        )
        encoding = _detect_encoding(data[:4096])

        if not self.blob_store.exists(sha256):
            self.blob_store.put_bytes(sha256, data)

        created_at = iso_now()
        stored_original_path = (
            original_path if self.config.security.store_original_path else None
        )
        artifact_meta = ArtifactMeta(
            sha256=sha256,
            size_bytes=size_bytes,
            mime=inferred_mime,
            created_at=created_at,
            original_name=original_name,
            original_path=stored_original_path,
            label=label,
            session_id=session_id,
            trace_id=trace_id,
            agent_id=agent_id,
            encoding=encoding,
            meta_json=meta,
        )
        self.index.upsert_artifact(artifact_meta)

        if auto_generate:
            self._auto_generate_views(sha256)
        ref = artifact_meta.to_ref()
        self._emit_retrieve_ingest_event(
            retrieve_ctl=retrieve_ctl,
            artifact_ref=ref,
            artifact_text=_extract_ingest_text_from_bytes(data),
            meta=meta,
            fallback_title=label or original_name,
        )
        return ref

    def _emit_retrieve_ingest_event(
        self,
        *,
        retrieve_ctl: Any | None,
        artifact_ref: ArtifactRef,
        artifact_text: str,
        meta: dict[str, Any] | None,
        fallback_title: str | None,
    ) -> None:
        if retrieve_ctl is None:
            return

        payload_meta = meta if isinstance(meta, dict) else {}
        scope = str(payload_meta.get("scope") or "project")
        title = str(payload_meta.get("title") or fallback_title or artifact_ref.ref)
        raw_tags = payload_meta.get("tags")
        tags: list[str] = []
        if isinstance(raw_tags, list):
            for value in raw_tags:
                text = str(value).strip()
                if text:
                    tags.append(text)
        if "artifact" not in tags:
            tags.insert(0, "artifact")

        payload = {
            "artifact_ref": artifact_ref.ref,
            "text": artifact_text,
            "scope": scope,
            "title": title,
            "tags": tags or ["artifact"],
        }
        try:
            retrieve_ctl.ingest_event("artifact.created", payload)
        except Exception:
            return

    def _resolve_sha(self, ref_or_sha: str) -> str:
        raw = (ref_or_sha or "").strip()
        if not raw:
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", "Expected artifact ref, sha256, or alias"
            )

        try:
            return parse_ref_or_sha(raw)
        except ValueError:
            resolved = self.index.alias_resolve(raw)
            if resolved is not None:
                return resolved.sha256
            raise ArtifactCtlError(
                "NOT_FOUND", f"Unknown artifact ref/sha/alias: {ref_or_sha}"
            )

    def _auto_generate_views(self, sha256: str) -> None:
        if not self.config.views.auto_generate:
            return
        for view_type in self.config.views.auto_generate:
            try:
                self.ensure_view(sha256, view_type)
            except ArtifactCtlError as exc:
                if exc.code in {"UNSUPPORTED_VIEW", "VIEW_TOO_LARGE"}:
                    continue
                logger.warning(
                    "view generation failed for %s/%s: %s", sha256, view_type, exc
                )
            except Exception as exc:
                logger.warning(
                    "view generation failed for %s/%s: %s", sha256, view_type, exc
                )

    def _enforce_ingest_size(self, size_bytes: int, *, source: str) -> None:
        max_bytes = int(self.config.blob_store.max_ingest_bytes)
        if size_bytes <= max_bytes:
            return
        raise ArtifactCtlError(
            "PAYLOAD_TOO_LARGE",
            f"Artifact payload exceeds configured limit for {source}",
            {"size_bytes": int(size_bytes), "max_ingest_bytes": max_bytes},
        )

    def _validate_owner_type(self, owner_type: str) -> None:
        if owner_type in VALID_OWNER_TYPES:
            return
        raise ArtifactCtlError("INVALID_ARGUMENT", f"Invalid owner_type: {owner_type}")

    def _generate_view_payload(
        self, raw_meta: ArtifactMeta, view_type: str
    ) -> tuple[bytes, str]:
        raw = self.read_bytes(raw_meta.sha256)

        if view_type == "text":
            text, _warnings = _decode_text(raw)
            if self.config.security.redaction_enabled:
                text = _redact_text(text)
            return text.encode("utf-8"), "text/plain"

        if view_type == "json":
            text, warnings = _decode_text(raw)
            if warnings:
                raise ArtifactCtlError(
                    "UNSUPPORTED_VIEW",
                    "JSON view unavailable for binary artifact",
                    {"warnings": warnings},
                )
            if len(text) > self.config.views.json_max_chars:
                raise ArtifactCtlError(
                    "VIEW_TOO_LARGE",
                    "JSON view input exceeds configured limit",
                    {"json_max_chars": self.config.views.json_max_chars},
                )
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ArtifactCtlError(
                    "UNSUPPORTED_VIEW", f"Artifact is not valid JSON: {exc}"
                ) from exc
            normalized = json.dumps(parsed, ensure_ascii=True, sort_keys=True, indent=2)
            return normalized.encode("utf-8"), "application/json"

        if view_type == "table":
            if raw_meta.mime not in {"text/csv", "text/tab-separated-values"}:
                raise ArtifactCtlError(
                    "UNSUPPORTED_VIEW",
                    f"Table view unsupported for mime: {raw_meta.mime}",
                )
            text, warnings = _decode_text(raw)
            if warnings:
                raise ArtifactCtlError(
                    "UNSUPPORTED_VIEW",
                    "Table view unavailable for binary artifact",
                    {"warnings": warnings},
                )
            delimiter = "\t" if raw_meta.mime == "text/tab-separated-values" else ","
            payload = _build_table_view(
                text, delimiter=delimiter, max_rows=self.config.views.table_max_rows
            )
            return json.dumps(
                payload, ensure_ascii=True, sort_keys=True, indent=2
            ).encode("utf-8"), "application/json"

        digest_payload = self._build_digest(raw_meta, raw)
        return json.dumps(
            digest_payload, ensure_ascii=True, sort_keys=True, indent=2
        ).encode("utf-8"), "application/json"

    def _build_digest(self, raw_meta: ArtifactMeta, data: bytes) -> dict[str, Any]:
        text, warnings = _decode_text(data)
        if self.config.security.redaction_enabled:
            text = _redact_text(text)

        excerpt, excerpt_warnings = _bounded_excerpt(
            text,
            max_lines=self.config.views.digest_max_lines,
            max_chars=self.config.views.digest_max_chars,
        )
        warnings.extend(excerpt_warnings)

        stats: dict[str, Any] = {}
        if text:
            stats["line_count"] = text.count("\n") + 1

        if raw_meta.mime == "application/json" and text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    stats["json_keys_count"] = len(parsed)
            except Exception:
                warnings.append("json_unparseable")

        if raw_meta.mime in {"text/csv", "text/tab-separated-values"} and text:
            delim = "\t" if raw_meta.mime == "text/tab-separated-values" else ","
            table_info = _build_table_view(
                text, delimiter=delim, max_rows=self.config.views.table_max_rows
            )
            stats["table_rows_sampled"] = int(table_info.get("sampled_rows", 0))

        summary = None
        for line in excerpt.splitlines():
            stripped = line.strip()
            if stripped:
                summary = stripped[:200]
                break

        payload: dict[str, Any] = {
            "artifact_sha256": raw_meta.sha256,
            "mime": raw_meta.mime,
            "size_bytes": raw_meta.size_bytes,
            "created_at": raw_meta.created_at,
            "excerpt": excerpt,
            "warnings": warnings,
        }
        if raw_meta.original_name:
            payload["original_name"] = raw_meta.original_name
        if raw_meta.label:
            payload["label"] = raw_meta.label
        if summary:
            payload["summary"] = summary
        if stats:
            payload["stats"] = stats
        return payload


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _sha256_path(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_sample(path: Path, n: int = 4096) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


def _determine_mime(provided: str | None, path: Path | None, sample: bytes) -> str:
    if provided:
        return provided
    if path is not None:
        guessed, _ = mimetypes.guess_type(str(path))
        if guessed:
            return guessed
        fallback = _FALLBACK_MIME_BY_SUFFIX.get(path.suffix.lower())
        if fallback:
            return fallback

    normalized_sample = sample
    if normalized_sample.startswith(b"\xef\xbb\xbf"):
        normalized_sample = normalized_sample[3:]
    normalized_sample = normalized_sample.lstrip()

    if normalized_sample.startswith(b"{") or normalized_sample.startswith(b"["):
        return "application/json"

    if not normalized_sample:
        return "application/octet-stream"

    if b"\x00" in normalized_sample:
        return "application/octet-stream"

    if _is_probably_text(normalized_sample):
        return "text/plain"

    return "application/octet-stream"


def _decode_text(data: bytes) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not data:
        return "", warnings

    if b"\x00" in data[:4096]:
        warnings.append("binary_content")
        return "", warnings

    try:
        return data.decode("utf-8"), warnings
    except UnicodeDecodeError:
        warnings.append("decode_errors_replaced")
        return data.decode("utf-8", errors="replace"), warnings


def _detect_encoding(data: bytes) -> str | None:
    if not data:
        return None
    _text, warnings = _decode_text(data)
    if "binary_content" in warnings:
        return "binary"
    if "decode_errors_replaced" in warnings:
        return "utf-8-lossy"
    return "utf-8"


def _extract_ingest_text_from_bytes(data: bytes, *, max_chars: int = 20000) -> str:
    text, _warnings = _decode_text(data)
    if not text:
        return ""
    return text[:max_chars].strip()


def _extract_ingest_text_from_file(
    path: Path, *, max_bytes: int = 256000, max_chars: int = 20000
) -> str:
    with path.open("rb") as fh:
        data = fh.read(max_bytes)
    return _extract_ingest_text_from_bytes(data, max_chars=max_chars)


def _is_probably_text(sample: bytes) -> bool:
    if not sample:
        return True
    printable = 0
    for b in sample:
        if b in {9, 10, 13} or 32 <= b <= 126:
            printable += 1
    ratio = printable / len(sample)
    return ratio >= 0.85


def _bounded_excerpt(
    text: str, *, max_lines: int, max_chars: int
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not text:
        return "", warnings

    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        warnings.append("truncated_lines")

    excerpt = "\n".join(lines)
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars]
        warnings.append("truncated_chars")

    return excerpt, warnings


def _redact_text(text: str) -> str:
    redacted = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    return _LONG_NUM_RE.sub(
        lambda match: _redact_long_number_match(redacted, match), redacted
    )


def _redact_long_number_match(text: str, match: re.Match[str]) -> str:
    candidate = match.group(0)
    if _is_iso_timestamp_context(text, match.start(), match.end()):
        return candidate
    if not _passes_luhn(candidate):
        return candidate
    return "[REDACTED_NUMBER]"


def _is_iso_timestamp_context(text: str, start: int, end: int) -> bool:
    context_start = max(0, start - 16)
    context_end = min(len(text), end + 10)
    context = text[context_start:context_end]
    for candidate in _ISO_TIMESTAMP_RE.finditer(context):
        absolute_start = context_start + candidate.start()
        absolute_end = context_start + candidate.end()
        if absolute_start <= start and end <= absolute_end:
            return True
    return False


def _passes_luhn(candidate: str) -> bool:
    digits = [int(ch) for ch in candidate if ch.isdigit()]
    if len(digits) < 12:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        value = digit
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        checksum += value
    return checksum % 10 == 0


def _build_table_view(text: str, *, delimiter: str, max_rows: int) -> dict[str, Any]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, [])
    sampled_rows: list[list[str]] = []
    total_rows = 0

    for row in reader:
        total_rows += 1
        if len(sampled_rows) < max_rows:
            sampled_rows.append(row)

    warnings: list[str] = []
    if total_rows > max_rows:
        warnings.append("rows_sampled")

    return {
        "columns": header,
        "rows": sampled_rows,
        "sampled_rows": len(sampled_rows),
        "total_rows": total_rows,
        "warnings": warnings,
    }


def _policy_hash(policy: dict[str, Any] | None) -> str:
    if not policy:
        return ""
    encoded = json.dumps(policy, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
