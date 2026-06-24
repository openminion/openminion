"""Memory service mutation operations."""

# mypy: disable-error-code="attr-defined,no-any-return,no-untyped-def"

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from openminion.modules.memory.errors import InvalidArgumentError, NotFoundError
from openminion.modules.memory.models import (
    ArtifactRef,
    MemoryCandidate,
    MemoryNamespace,
    MemoryRecord,
    MemoryScope,
    _as_memory_type,
)
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.storage.base import CandidateListOptions


def _set_namespace_value(
    values: dict[str, str],
    key: str,
    value: str | None,
) -> None:
    normalized = str(value or "").strip()
    if not normalized:
        return
    existing = values.get(key)
    if existing is not None and existing != normalized:
        raise InvalidArgumentError(
            f"conflicting namespace {key}: {existing!r} != {normalized!r}"
        )
    values[key] = normalized


def _resolve_explicit_namespace(
    *,
    scope: str,
    namespace: MemoryNamespace | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    conversation_id: str | None = None,
    project_id: str | None = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    org_id: str | None = None,
    graph_id: str | None = None,
) -> MemoryNamespace:
    values = namespace.as_dict() if namespace is not None else {}
    parsed_scope = MemoryScope.parse(scope)
    if parsed_scope.kind == "session":
        _set_namespace_value(values, "session_id", parsed_scope.value)
    elif parsed_scope.kind == "agent":
        _set_namespace_value(values, "agent_id", parsed_scope.value)
    elif parsed_scope.kind == "project":
        _set_namespace_value(values, "project_id", parsed_scope.value)
    else:
        _set_namespace_value(values, "graph_id", parsed_scope.value)

    for key, value in (
        ("agent_id", agent_id),
        ("session_id", session_id),
        ("conversation_id", conversation_id),
        ("project_id", project_id),
        ("user_id", user_id),
        ("tenant_id", tenant_id),
        ("org_id", org_id),
        ("graph_id", graph_id),
    ):
        _set_namespace_value(values, key, value)
    return MemoryNamespace.from_dict(values)


class MemoryServiceMutationMixin:
    def write_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        agent_id: str | None = None,
        namespace: MemoryNamespace | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        org_id: str | None = None,
        graph_id: str | None = None,
    ) -> str:
        if agent_id:
            from openminion.modules.memory.runtime.scope import (
                assert_scope_matches_agent,
            )

            assert_scope_matches_agent(scope, agent_id)
        normalized_scope = str(MemoryScope.coerce(scope))
        resolved_namespace = _resolve_explicit_namespace(
            scope=normalized_scope,
            namespace=namespace,
            agent_id=agent_id,
            session_id=session_id,
            conversation_id=conversation_id,
            project_id=project_id,
            user_id=user_id,
            tenant_id=tenant_id,
            org_id=org_id,
            graph_id=graph_id,
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        record = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            scope=normalized_scope,
            type=_as_memory_type(record_type),
            title=title,
            content=content,
            tags=list(tags or []),
            confidence=float(confidence) if confidence is not None else 0.5,
            evidence_refs=[
                ArtifactRef(
                    ref=str(ref),
                    mime="application/octet-stream",
                    sha256="unknown",
                    size_bytes=0,
                    label=f"evidence-{ref}",
                )
                for ref in evidence_refs or []
            ],
            namespace=resolved_namespace,
            created_at=now_iso,
            updated_at=now_iso,
        )
        return self._store.put(record)

    def export_bundle_snapshot(
        self,
        options: MemoryBundleExportOptions,
    ) -> MemoryBundleSnapshot:
        return self._bundle_helper().export_bundle_snapshot(options)

    def import_bundle_snapshot(
        self,
        snapshot: MemoryBundleSnapshot,
        options: MemoryBundleImportOptions,
    ) -> MemoryBundleImportResult:
        return self._bundle_helper().import_bundle_snapshot(snapshot, options)

    def delete_record(
        self,
        record_id: str,
        *,
        reason: str | None = None,
    ) -> bool:
        return self._bundle_helper().delete_record(record_id, reason=reason)

    def forget_by_source(
        self,
        source: str,
        *,
        reason: str,
        dry_run: bool = True,
    ) -> list[str]:
        return self._bundle_helper().forget_by_source(
            source,
            reason=reason,
            dry_run=dry_run,
        )

    def _iter_all_records_for_forget(self):
        yield from self._bundle_helper()._iter_all_records_for_forget()

    def upsert_record(
        self,
        *,
        scope: str,
        record_type: str,
        key: str,
        record_patch: dict[str, Any],
        namespace: MemoryNamespace | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        org_id: str | None = None,
        graph_id: str | None = None,
    ) -> MemoryRecord:
        normalized_scope = str(MemoryScope.coerce(scope))
        patch = dict(record_patch)
        patch["namespace"] = _resolve_explicit_namespace(
            scope=normalized_scope,
            namespace=namespace,
            agent_id=agent_id,
            session_id=session_id,
            conversation_id=conversation_id,
            project_id=project_id,
            user_id=user_id,
            tenant_id=tenant_id,
            org_id=org_id,
            graph_id=graph_id,
        )
        return self._store.upsert(
            normalized_scope,
            _as_memory_type(record_type),
            key,
            patch,
        )

    def candidate_put(self, candidate: MemoryCandidate) -> str:
        return self._candidate_helper().candidate_put(candidate)

    def stage_candidate(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
        meta: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> str:
        return self._candidate_helper().stage_candidate(
            scope=scope,
            record_type=record_type,
            title=title,
            content=content,
            tags=tags,
            evidence_refs=evidence_refs,
            confidence=confidence,
            meta=meta,
            agent_id=agent_id,
        )

    def candidate_get(self, candidate_id: str) -> MemoryCandidate:
        return self._candidate_helper().candidate_get(candidate_id)

    def candidate_list(self, options: CandidateListOptions) -> list[MemoryCandidate]:
        return self._candidate_helper().candidate_list(options)

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate:
        return self._candidate_helper().candidate_update(candidate_id, patch)

    def find_candidate_by_normalized_key(
        self, *, scope: str, normalized_key: str
    ) -> str | None:
        return self._candidate_helper().find_candidate_by_normalized_key(
            scope=scope,
            normalized_key=normalized_key,
        )

    def reinforce_candidate(self, *, candidate_id: str) -> MemoryCandidate:
        return self._candidate_helper().reinforce_candidate(candidate_id=candidate_id)

    def find_record_by_normalized_key(
        self,
        *,
        scope: str,
        record_type: str,
        normalized_key: str,
    ) -> MemoryRecord | None:
        return self._candidate_helper().find_record_by_normalized_key(
            scope=scope,
            record_type=record_type,
            normalized_key=normalized_key,
        )

    def reinforce_record(self, *, record_id: str) -> MemoryRecord:
        return self._candidate_helper().reinforce_record(record_id=record_id)

    def promote_candidate(self, candidate_id: str, target_scope: str) -> MemoryRecord:
        return self._candidate_helper().promote_candidate(
            candidate_id,
            target_scope,
        )

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord:
        handler = getattr(self._store, "supersede_by_contradiction", None)
        if not callable(handler):
            raise InvalidArgumentError(
                "semantic supersession is unsupported by the configured memory store"
            )
        try:
            return handler(old_record_id, new_record_id, reason=reason)
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise InvalidArgumentError(str(exc)) from exc

    def invalidate(
        self,
        memory_id: str,
        *,
        valid_to: datetime | str | None = None,
        reason: str,
    ) -> MemoryRecord:
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise InvalidArgumentError("invalidate requires a non-empty reason")
        handler = getattr(self._store, "invalidate", None)
        if not callable(handler):
            raise InvalidArgumentError(
                "temporal invalidation is unsupported by the configured memory store"
            )
        if valid_to is None:
            normalized_valid_to = datetime.now(timezone.utc).isoformat()
        elif isinstance(valid_to, datetime):
            normalized_valid_to = (
                valid_to
                if valid_to.tzinfo is not None
                else valid_to.replace(tzinfo=timezone.utc)
            ).isoformat()
        else:
            normalized_valid_to = str(valid_to).strip()
        if not normalized_valid_to:
            raise InvalidArgumentError("invalidate requires a valid_to timestamp")
        try:
            return handler(
                memory_id,
                valid_to=normalized_valid_to,
                reason=normalized_reason,
            )
        except ValueError as exc:
            if "not found" in str(exc).lower():
                raise NotFoundError(str(exc)) from exc
            raise InvalidArgumentError(str(exc)) from exc
