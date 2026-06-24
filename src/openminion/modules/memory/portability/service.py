"""Bundle and batch-forget helpers for ``MemoryService``."""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
import uuid

from openminion.base.time import utc_now_iso as _now_iso
from openminion.modules.memory.contracts.provenance import TurnProvenanceTrace
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.portability.codec import build_manifest
from openminion.modules.memory.portability.merger import MemoryMerger
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.runtime.provenance import (
    default_provenance_recorder,
)
from openminion.modules.memory.models import _as_memory_type_list
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)


class MemoryBundleServiceOps:
    """Internal owner for portability and bulk-forget flows."""

    _FORGET_PAGE_SIZE: int = 500

    def __init__(self, service: Any) -> None:
        self._service = service

    def export_bundle_snapshot(
        self,
        options: MemoryBundleExportOptions,
    ) -> MemoryBundleSnapshot:
        normalized_scopes = [
            str(scope or "").strip()
            for scope in options.scopes
            if str(scope or "").strip()
        ]
        records = self._service.list(
            ListQueryOptions(
                scopes=normalized_scopes,
                types=_as_memory_type_list(options.types),
                include_invalidated=True,
                limit=options.limit,
                namespaces=options.namespaces,
            )
        )
        candidates = self._collect_candidates(
            normalized_scopes=normalized_scopes,
            include_candidates=options.include_candidates,
        )
        relations = self._collect_relations(
            records=records,
            include_relations=options.include_relations,
        )
        tier_transitions = []
        if options.include_tier_history:
            tier_transitions = self._service.list_tier_transitions(
                scopes=normalized_scopes,
                limit=None,
            )
        provenance_traces = self._collect_provenance_traces(
            normalized_scopes=normalized_scopes,
            include_provenance=options.include_provenance,
        )
        store = getattr(self._service._store, "_store", self._service._store)  # noqa: SLF001
        store_name = type(store).__name__
        snapshot = MemoryBundleSnapshot(
            manifest={
                "bundle_id": f"mb_{uuid.uuid4().hex[:12]}",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_backend": store_name,
                "source_instance": {
                    "store_class": store_name,
                    "scopes": normalized_scopes,
                },
                "scopes": normalized_scopes,
                "filters": {
                    "types": list(options.types or []),
                    "limit": options.limit,
                    "include_provenance": bool(options.include_provenance),
                },
            },
            records=records,
            candidates=candidates,
            relations=relations,
            tier_transitions=tier_transitions,
            provenance_traces=provenance_traces,
        )
        return replace(snapshot, manifest=build_manifest(snapshot=snapshot))

    def _collect_candidates(
        self,
        *,
        normalized_scopes: list[str],
        include_candidates: bool,
    ) -> list[Any]:
        if not include_candidates:
            return []
        candidates: list[Any] = []
        for scope in normalized_scopes:
            candidates.extend(
                self._service.candidate_list(
                    CandidateListOptions(
                        proposed_scope=scope,
                        limit=None,
                    )
                )
            )
        return candidates

    def _collect_relations(
        self,
        *,
        records: list[Any],
        include_relations: bool,
    ) -> list[Any]:
        if not include_relations:
            return []
        relations: list[Any] = []
        seen_relation_ids: set[str] = set()
        for record in records:
            for relation in self._service.list_relations(
                record_id=record.id, limit=None
            ):
                if relation.relation_id in seen_relation_ids:
                    continue
                seen_relation_ids.add(relation.relation_id)
                relations.append(relation)
        return relations

    def _collect_provenance_traces(
        self,
        *,
        normalized_scopes: list[str],
        include_provenance: bool,
    ) -> list[TurnProvenanceTrace]:
        if not include_provenance:
            return []
        requested_session_ids = {
            scope[len("session:") :]
            for scope in normalized_scopes
            if scope.startswith("session:")
        }
        provenance_traces: list[TurnProvenanceTrace] = []
        for trace in default_provenance_recorder().iter_all_traces():
            if requested_session_ids and trace.session_id not in requested_session_ids:
                continue
            provenance_traces.append(trace)
        return provenance_traces

    def import_bundle_snapshot(
        self,
        snapshot: MemoryBundleSnapshot,
        options: MemoryBundleImportOptions,
    ) -> MemoryBundleImportResult:
        return MemoryMerger(self._service).import_snapshot(snapshot, options)

    def delete_record(
        self,
        record_id: str,
        *,
        reason: str | None = None,
    ) -> bool:
        existing = self._service._store.get(record_id)  # noqa: SLF001
        if existing is None:
            return False
        if reason is None:
            self._service._store.delete(record_id)  # noqa: SLF001
            return True
        try:
            self._service._store.delete(  # noqa: SLF001
                record_id,
                reason=reason,
                deleted_at=_now_iso(),
            )
        except TypeError:
            self._service._store.delete(record_id)  # noqa: SLF001
        return True

    def forget_by_source(
        self,
        source: str,
        *,
        reason: str,
        dry_run: bool = True,
    ) -> list[str]:
        if not reason or not reason.strip():
            raise InvalidArgumentError("forget_by_source requires a non-empty reason")
        normalized_source = str(source or "").strip()
        if not normalized_source:
            raise InvalidArgumentError("forget_by_source requires a non-empty source")
        matched = [
            record.id
            for record in self._iter_all_records_for_forget()
            if not record.is_deleted
            and str(getattr(record, "source", "") or "").strip() == normalized_source
        ]
        if not dry_run:
            for record_id in matched:
                self.delete_record(record_id, reason=reason)
        return matched

    def _iter_all_records_for_forget(self):
        if hasattr(self._service._store, "iter_all_records"):  # noqa: SLF001
            yield from self._service._store.iter_all_records()  # noqa: SLF001
            return
        if hasattr(self._service._store, "list_all"):  # noqa: SLF001
            yield from self._service._store.list_all()  # noqa: SLF001
            return
        if hasattr(self._service._store, "list_scopes") and hasattr(  # noqa: SLF001
            self._service._store,
            "list",  # noqa: SLF001
        ):
            from openminion.modules.memory.storage.base import RecordOrder

            seen_ids: set[str] = set()
            try:
                scopes = list(self._service._store.list_scopes())  # noqa: SLF001
            except Exception:
                scopes = []
            for scope_value in scopes:
                offset = 0
                while True:
                    try:
                        page = self._service._store.list(  # noqa: SLF001
                            ListQueryOptions(
                                scopes=[scope_value],
                                include_invalidated=True,
                                limit=self._FORGET_PAGE_SIZE,
                                offset=offset,
                                order_by=RecordOrder.UPDATED_AT_ASC,
                            )
                        )
                    except TypeError:
                        page = []
                    if not page:
                        break
                    for record in page:
                        if record.id in seen_ids:
                            continue
                        seen_ids.add(record.id)
                        yield record
                    if len(page) < self._FORGET_PAGE_SIZE:
                        break
                    offset += self._FORGET_PAGE_SIZE
            return
        raise NotImplementedError(
            "forget_by_source requires the memory store to expose "
            "iter_all_records(), list_all(), or list_scopes()+list(); "
            f"current store {type(self._service._store).__name__} exposes none of them."  # noqa: SLF001
        )
