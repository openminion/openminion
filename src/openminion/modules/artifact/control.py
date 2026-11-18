"""Artifact control surface for ingest, lookup, verification, and retention."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, cast
from uuid import uuid4

from openminion.base.time import utc_now_iso as iso_now
from openminion.modules.artifact.content import (
    _determine_mime,
    _bounded_excerpt,
    _build_table_view,
    _decode_text,
    _detect_encoding,
    _extract_ingest_text_from_bytes,
)
from openminion.modules.artifact.config import (
    ArtifactCtlConfig,
    load_config,
    retention_override,
    validate_retention_config,
)
from openminion.modules.artifact.constants import (
    DEFAULT_CONFIG_FILENAME,
    VALID_OWNER_TYPES,
)
from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.integrity import build_verify_report, hash_file
from openminion.modules.artifact.integrity import read_file_sample
from openminion.modules.artifact.interfaces import ARTIFACT_INTERFACE_VERSION
from openminion.modules.artifact.models import (
    ArtifactMeta,
    ImageRef,
    ArtifactRef,
    GCReport,
    PurgeReport,
    VerifyReport,
    ViewRecord,
    normalize_alias_expiry,
    parse_ref_or_sha,
)
from openminion.modules.artifact.storage import (
    FileSystemCASBlobStore,
    SQLiteArtifactIndex,
)

_SCHEMA_VERSION = "v1"
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
_LONG_NUM_RE = re.compile(r"(?<![0-9A-Fa-f])\d{12,19}(?![0-9A-Fa-f])")
_ISO_TIMESTAMP_RE = re.compile(
    r"\d{4}-?\d{2}-?\d{2}T\d{2,14}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
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
        validate_retention_config(self.config)

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

        self.blob_store: FileSystemCASBlobStore = FileSystemCASBlobStore(
            self.config.blob_store.root_dir
        )
        self.index: SQLiteArtifactIndex = SQLiteArtifactIndex(
            sqlite_path=self.config.index.sqlite_path,
            wal=self.config.index.wal,
        )

    def __enter__(self) -> ArtifactCtl:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def ingest_image(self, data: bytes, *, mime: str, session_id: str) -> ImageRef:
        from .images import normalize_image

        if not session_id:
            raise ArtifactCtlError("INVALID_ARGUMENT", "Images require a session")
        self._enforce_ingest_size(len(data), source="selected image")
        normalized, width, height = normalize_image(data, mime)
        self._enforce_ingest_size(len(normalized), source="normalized image")
        sha = hashlib.sha256(normalized).hexdigest()
        self._check_ingest_policy(sha, opaque=True)
        self.blob_store.put_bytes(sha, normalized)
        if self.index.get_artifact(sha) is None:
            self.index.upsert_artifact(
                ArtifactMeta(
                    sha256=sha,
                    size_bytes=len(normalized),
                    mime="image/png",
                    created_at=iso_now(),
                    session_id=session_id,
                    meta_json={"opaque": True},
                )
            )
        image = ImageRef(
            f"image://{uuid4()}", sha, len(normalized), width, height, len(data)
        )
        self.index.bind_image(image, session_id)
        return image

    def get_image(self, image_ref: str, *, session_id: str) -> ImageRef:
        image = self.index.get_image(image_ref, session_id)
        if image is None or not self.blob_store.exists(image.sha256):
            raise ArtifactCtlError(
                "IMAGE_UNAVAILABLE",
                "Image is unavailable in this session; select it again",
            )
        return image

    def read_image(self, image_ref: str, *, session_id: str) -> bytes:
        image = self.get_image(image_ref, session_id=session_id)
        with self.blob_store.get_stream(image.sha256) as stream:
            return cast(bytes, stream.read())

    def revoke_image(self, image_ref: str, *, session_id: str) -> None:
        self.index.revoke_image(image_ref, session_id)

    def revoke_session_images(self, session_id: str) -> None:
        self.index.revoke_session_images(session_id)

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
        opaque: bool = False,
    ) -> ArtifactRef:
        if opaque and not session_id:
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", "Opaque inputs require a session"
            )
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
            auto_generate=not opaque,
            retrieve_ctl=retrieve_ctl,
            opaque=opaque,
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
        if "opaque" in (meta or {}):
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", "Opaque policy is not caller metadata"
            )
        src = Path(path).expanduser().resolve(strict=True)
        self._enforce_ingest_size(src.stat().st_size, source=str(src))
        sha256, size_bytes = hash_file(src)
        self._check_ingest_policy(sha256, opaque=False)
        sample = read_file_sample(src, 256000)
        sniff = sample[:4096]
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
        self._check_ingest_policy(sha256, opaque=False)

        self._auto_generate_views(sha256)
        ref = self.get(sha256).to_ref()
        if retrieve_ctl is not None:
            self._emit_retrieve_ingest_event(
                retrieve_ctl=retrieve_ctl,
                artifact_ref=ref,
                artifact_text=_extract_ingest_text_from_bytes(sample),
                artifact_meta=artifact_meta,
                fallback_title=label or src.name,
            )
        return ref

    def get(self, ref_or_sha: str) -> ArtifactMeta:
        sha = self._resolve_sha(ref_or_sha)
        meta = self.index.get_artifact(sha, include_deleted=True)
        if meta is None:
            raise ArtifactCtlError("NOT_FOUND", f"Artifact not found: {ref_or_sha}")
        return meta

    def open(self, ref_or_sha: str, *, session_id: str | None = None) -> BinaryIO:
        sha = self._resolve_active_sha(ref_or_sha)
        meta = self.get(sha)
        if (meta.meta_json or {}).get("opaque") and (
            not session_id or session_id != meta.session_id
        ):
            raise ArtifactCtlError(
                "POLICY_DENIED", "Opaque input belongs to another session"
            )
        if not self.blob_store.exists(sha):
            raise ArtifactCtlError("NOT_FOUND", f"Blob is missing for artifact: {sha}")
        return self.blob_store.get_stream(sha)

    def read_bytes(self, ref_or_sha: str, *, session_id: str | None = None) -> bytes:
        with self.open(ref_or_sha, session_id=session_id) as fh:
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

        if (raw_meta.meta_json or {}).get("opaque"):
            raise ArtifactCtlError(
                "POLICY_DENIED", "Opaque inputs have no content views"
            )

        settings: dict[str, dict[str, Any]] = {
            "digest": {
                "redaction_enabled": self.config.security.redaction_enabled,
                "digest_max_chars": self.config.views.digest_max_chars,
                "digest_max_lines": self.config.views.digest_max_lines,
                "table_max_rows": self.config.views.table_max_rows,
            },
            "text": {
                "redaction_enabled": self.config.security.redaction_enabled,
                "text_max_chars": self.config.views.text_max_chars,
            },
            "json": {"json_max_chars": self.config.views.json_max_chars},
            "table": {"table_max_rows": self.config.views.table_max_rows},
        }
        policy_hash = _policy_hash({"policy": policy or {}, **settings[normalized]})
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
        extension = "json" if mime == "application/json" else "txt"
        view_name = f"{raw_sha}.{normalized}.{extension}"
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
        if expires_at is not None:
            try:
                expires_at = normalize_alias_expiry(expires_at)
            except ValueError as exc:
                raise ArtifactCtlError("INVALID_ARGUMENT", str(exc)) from exc

        existing = self.index.alias_resolve(normalized_alias)
        if existing and not overwrite:
            raise ArtifactCtlError("ALREADY_EXISTS", f"Alias already exists: {alias}")

        sha = self._resolve_active_sha(ref_or_sha)
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
        return None if meta is None else meta.to_ref()

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
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[ArtifactMeta]:
        return self.index.search(query=query, filters=filters, limit=limit)

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
        keep = (
            self.config.retention.keep_days
            if keep_days is None
            else retention_override(keep_days, "keep_days")
        )
        delete_after = (
            self.config.retention.delete_unreferenced_after_days
            if delete_unreferenced_after_days is None
            else retention_override(
                delete_unreferenced_after_days,
                "delete_unreferenced_after_days",
            )
        )
        root_set = self.index.active_reference_shas() | self.index.active_alias_shas()
        protected = root_set | self.index.recent_artifact_shas(keep)
        protected |= {
            view.view_sha256
            for raw_sha in tuple(protected)
            for view in self.index.list_views(raw_sha)
            if view.view_sha256
        }
        eligible = self.index.eligible_for_gc(delete_after, protected)
        marked = 0
        if not plan_only and eligible:
            now = iso_now()
            marked = self.index.soft_delete_artifacts(eligible, now)
            self._soft_delete_derived_views(eligible, now)
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
        self.index.revoke_images_for_blob(sha)
        derived_shas = self._soft_delete_derived_views([sha], now)
        if sha not in self.index.active_view_shas():
            self.index.soft_delete_artifacts([sha], now)
            if not soft:
                self.blob_store.delete(sha)
        if not soft:
            for derived_sha in derived_shas:
                self.blob_store.delete(derived_sha)

    def purge(self, grace_days: int | None = None) -> PurgeReport:
        grace = (
            self.config.retention.purge_grace_days
            if grace_days is None
            else retention_override(grace_days, "grace_days")
        )
        views = self.index.purgeable_views(grace)
        protected = self.index.active_reference_shas() | self.index.active_alias_shas()
        protected.update(self.index.retained_view_shas(grace))
        view_shas = {
            view.view_sha256
            for view in views
            if view.view_sha256 and view.view_sha256 not in protected
        }
        for view in views:
            if view.view_sha256 in view_shas:
                self.index.soft_delete_artifacts(
                    [view.view_sha256], view.deleted_at or iso_now()
                )
        artifacts = [
            artifact
            for artifact in self.index.purgeable_artifacts(grace)
            if artifact.sha256 not in protected
        ]
        view_shas.intersection_update(artifact.sha256 for artifact in artifacts)
        missing = 0
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
                if view.view_sha256 in view_shas:
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
        for view in views:
            self.index.hard_delete_view(view)
        for artifact in artifacts:
            self.index.hard_delete_artifact(artifact.sha256)
        self.index.hard_delete_expired_aliases(grace)
        self.index.hard_delete_reference_tombstones(grace)
        return PurgeReport(
            grace_days=grace,
            purged_views=purged_views,
            purged_blobs=purged_blobs,
            missing_files=missing,
        )

    def _soft_delete_derived_views(self, shas: list[str], deleted_at: str) -> set[str]:
        candidates: set[str] = {
            str(view.view_sha256)
            for raw_sha in shas
            for view in self.index.list_views(raw_sha)
            if view.view_sha256
        }
        for raw_sha in shas:
            self.index.soft_delete_views_for_raw(raw_sha, deleted_at)
        protected = self.index.active_reference_shas() | self.index.active_alias_shas()
        protected.update(self.index.active_view_shas())
        deletable: set[str] = candidates - protected
        self.index.soft_delete_artifacts(deletable, deleted_at)
        return deletable

    def verify(self, target: str = "all") -> VerifyReport:
        verify_all = (target or "all") == "all"
        if verify_all:
            rows = self.index.all_artifacts(include_deleted=False)
        else:
            rows = [self.get(target)]

        orphan_shas: list[str] = []
        if verify_all:
            indexed_shas = {
                row.sha256 for row in self.index.all_artifacts(include_deleted=True)
            }
            orphan_shas = sorted(set(self.blob_store.list_sha256()) - indexed_shas)
        return build_verify_report(
            rows,
            blob_store=self.blob_store,
            orphan_shas=orphan_shas,
        )

    def ref_add(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self._validate_owner_type(owner_type)
        if not owner_id or not owner_id.strip():
            raise ArtifactCtlError("INVALID_ARGUMENT", "owner_id cannot be blank")
        sha = self._resolve_active_sha(ref_or_sha)
        self.index.add_reference(owner_type, owner_id, sha)

    def ref_remove(self, owner_type: str, owner_id: str, ref_or_sha: str) -> None:
        self._validate_owner_type(owner_type)
        sha = self._resolve_sha(ref_or_sha)
        self.index.remove_reference(owner_type, owner_id, sha)

    def ref_remove_owner(self, owner_type: str, owner_id: str) -> None:
        self._validate_owner_type(owner_type)
        self.index.remove_references_for_owner(owner_type, owner_id)

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
        opaque: bool = False,
    ) -> ArtifactRef:
        self._enforce_ingest_size(len(data), source=original_name or "bytes")
        sha256 = hashlib.sha256(data).hexdigest()
        self._check_ingest_policy(sha256, opaque=opaque)
        if "opaque" in (meta or {}):
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", "Opaque policy is not caller metadata"
            )
        if opaque:
            meta = {**(meta or {}), "opaque": True}
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
        self._check_ingest_policy(sha256, opaque=opaque)

        if auto_generate:
            self._auto_generate_views(sha256)
        ref = self.get(sha256).to_ref()
        if not opaque and retrieve_ctl is not None:
            self._emit_retrieve_ingest_event(
                retrieve_ctl=retrieve_ctl,
                artifact_ref=ref,
                artifact_text=_extract_ingest_text_from_bytes(data),
                artifact_meta=artifact_meta,
                fallback_title=label or original_name,
            )
        return ref

    def _check_ingest_policy(self, sha256: str, *, opaque: bool) -> None:
        existing = self.index.get_artifact(sha256, include_deleted=True)
        if existing is None:
            return
        was_opaque = bool((existing.meta_json or {}).get("opaque"))
        if was_opaque != opaque:
            raise ArtifactCtlError(
                "POLICY_DENIED", "Existing artifact has a different input policy"
            )

    def _emit_retrieve_ingest_event(
        self,
        *,
        retrieve_ctl: Any,
        artifact_ref: ArtifactRef,
        artifact_text: str,
        artifact_meta: ArtifactMeta,
        fallback_title: str | None,
    ) -> None:
        if not artifact_text.strip():
            return

        payload_meta = artifact_meta.meta_json or {}
        scope = str(payload_meta.get("scope") or "project")
        title = str(payload_meta.get("title") or fallback_title or artifact_ref.ref)
        raw_tags = payload_meta.get("tags")
        tag_values = raw_tags if isinstance(raw_tags, list) else []
        tags = [text for value in tag_values if (text := str(value).strip())]
        if "artifact" not in tags:
            tags.insert(0, "artifact")

        if self.config.security.redaction_enabled:
            artifact_text = _redact_text(artifact_text)
        payload = {
            "artifact_ref": artifact_ref.ref,
            "text": artifact_text,
            "scope": scope,
            "scope_key": payload_meta.get("scope_key"),
            "session_id": artifact_meta.session_id or payload_meta.get("session_id"),
            "agent_id": artifact_meta.agent_id or payload_meta.get("agent_id"),
            "project_id": payload_meta.get("project_id"),
            "title": title,
            "tags": tags,
        }
        try:
            retrieve_ctl.ingest_event("artifact.created", payload)
        except Exception as exc:
            logger.warning(
                "retrieve ingest failed: %s (%s)", artifact_ref.ref, type(exc).__name__
            )

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

    def _resolve_active_sha(self, ref_or_sha: str) -> str:
        sha = self._resolve_sha(ref_or_sha)
        if self.index.get_artifact(sha, include_deleted=False) is None:
            raise ArtifactCtlError("NOT_FOUND", f"Artifact not found: {ref_or_sha}")
        return sha

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
            if len(text) > self.config.views.text_max_chars:
                raise ArtifactCtlError(
                    "VIEW_TOO_LARGE",
                    "Text view input exceeds configured limit",
                    {"text_max_chars": self.config.views.text_max_chars},
                )
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


def _policy_hash(policy: dict[str, Any] | None) -> str:
    if not policy:
        return ""
    encoded = json.dumps(policy, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
