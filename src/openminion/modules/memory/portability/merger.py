from dataclasses import asdict, replace
import uuid
from typing import Any

from openminion.base.time import utc_now_iso as _now_iso
from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    MemoryRelation,
    MemoryTierTransition,
)

from .models import (
    MemoryBundleSnapshot,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
)
from ..errors import InvalidArgumentError


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _rewrite_scope(scope: str, rewrites: dict[str, str]) -> str:
    current = str(scope or "").strip()
    return str(rewrites.get(current, current))


def _record_equal(left: MemoryRecord, right: MemoryRecord, *, ignore_id: bool) -> bool:
    left_dict = asdict(left)
    right_dict = asdict(right)
    if ignore_id:
        left_dict.pop("id", None)
        right_dict.pop("id", None)
    for key in (
        "created_at",
        "updated_at",
        "supersedes_id",
        "superseded_by_id",
        "supersession_reason",
        "last_hit_at",
    ):
        left_dict.pop(key, None)
        right_dict.pop(key, None)
    return left_dict == right_dict


def _candidate_equal(
    left: MemoryCandidate,
    right: MemoryCandidate,
    *,
    ignore_id: bool,
) -> bool:
    left_dict = asdict(left)
    right_dict = asdict(right)
    if ignore_id:
        left_dict.pop("candidate_id", None)
        right_dict.pop("candidate_id", None)
    for key in ("created_at", "updated_at", "session_id"):
        left_dict.pop(key, None)
        right_dict.pop(key, None)
    return left_dict == right_dict


class MemoryMerger:
    """Bundle import engine layered on top of `MemoryService`."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def import_snapshot(
        self,
        snapshot: MemoryBundleSnapshot,
        options: MemoryBundleImportOptions,
    ) -> MemoryBundleImportResult:
        if options.trust_mode not in {"direct", "candidate"}:
            raise InvalidArgumentError("trust_mode must be 'direct' or 'candidate'")
        if options.conflict_mode not in {"skip", "supersede", "error"}:
            raise InvalidArgumentError(
                "conflict_mode must be 'skip', 'supersede', or 'error'"
            )
        if options.id_mode not in {"preserve", "regenerate"}:
            raise InvalidArgumentError("id_mode must be 'preserve' or 'regenerate'")
        manifest = dict(snapshot.manifest or {})
        bundle_id = str(manifest.get("bundle_id", "") or "")
        source_instance = manifest.get("source_instance", {})
        imported_at = _now_iso()

        record_id_map = (
            {}
            if options.trust_mode == "candidate"
            else self._prepare_record_id_map(snapshot.records, options)
        )
        rewritten_records = [
            self._rewrite_record(
                record,
                options=options,
                id_map=record_id_map,
                bundle_id=bundle_id,
                source_instance=source_instance,
                imported_at=imported_at,
            )
            for record in snapshot.records
        ]

        if options.trust_mode == "candidate":
            return self._import_candidate_mode(
                rewritten_records=rewritten_records,
                bundle_id=bundle_id,
                source_instance=source_instance,
                imported_at=imported_at,
                snapshot=snapshot,
                options=options,
            )

        return self._import_direct_mode(
            snapshot=snapshot,
            rewritten_records=rewritten_records,
            record_id_map=record_id_map,
            bundle_id=bundle_id,
            source_instance=source_instance,
            imported_at=imported_at,
            options=options,
        )

    def _prepare_record_id_map(
        self,
        records: list[MemoryRecord],
        options: MemoryBundleImportOptions,
    ) -> dict[str, str]:
        id_map: dict[str, str] = {}
        if options.id_mode == "regenerate":
            for record in records:
                id_map[record.id] = _new_id("mem")
            return id_map

        if options.conflict_mode != "supersede":
            return id_map

        for record in records:
            existing = self._service._store.get(record.id)  # noqa: SLF001
            if existing is None:
                continue
            if _record_equal(existing, record, ignore_id=False):
                continue
            id_map[record.id] = _new_id("mem")
        return id_map

    def _rewrite_record(
        self,
        record: MemoryRecord,
        *,
        options: MemoryBundleImportOptions,
        id_map: dict[str, str],
        bundle_id: str,
        source_instance: Any,
        imported_at: str,
    ) -> MemoryRecord:
        rewritten_meta = dict(record.meta or {})
        rewritten_meta.update(
            {
                "import_bundle_id": bundle_id,
                "imported_at": imported_at,
                "import_source_instance": source_instance,
            }
        )
        new_id = id_map.get(record.id, record.id)
        return replace(
            record,
            id=new_id,
            scope=_rewrite_scope(record.scope, options.scope_rewrites),  # type: ignore[arg-type]
            meta=rewritten_meta,
            supersedes_id=id_map.get(record.supersedes_id, record.supersedes_id)
            if record.supersedes_id
            else None,
            superseded_by_id=id_map.get(
                record.superseded_by_id,
                record.superseded_by_id,
            )
            if record.superseded_by_id
            else None,
        )

    def _rewrite_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        options: MemoryBundleImportOptions,
        bundle_id: str,
        source_instance: Any,
        imported_at: str,
    ) -> MemoryCandidate:
        candidate_id = (
            _new_id("cand")
            if options.id_mode == "regenerate"
            else candidate.candidate_id
        )
        rewritten_meta = dict(candidate.meta or {})
        rewritten_meta.update(
            {
                "import_bundle_id": bundle_id,
                "imported_at": imported_at,
                "import_source_instance": source_instance,
            }
        )
        return replace(
            candidate,
            candidate_id=candidate_id,
            proposed_scope=_rewrite_scope(
                candidate.proposed_scope,
                options.scope_rewrites,
            ),  # type: ignore[arg-type]
            meta=rewritten_meta,
        )

    def _rewrite_relation(
        self,
        relation: MemoryRelation,
        *,
        options: MemoryBundleImportOptions,
        record_id_map: dict[str, str],
    ) -> MemoryRelation | None:
        source_id = record_id_map.get(
            relation.source_record_id, relation.source_record_id
        )
        target_id = record_id_map.get(
            relation.target_record_id, relation.target_record_id
        )
        if not source_id or not target_id:
            return None
        relation_id = (
            _new_id("rel") if options.id_mode == "regenerate" else relation.relation_id
        )
        return replace(
            relation,
            relation_id=relation_id,
            source_record_id=source_id,
            target_record_id=target_id,
        )

    def _rewrite_tier_transition(
        self,
        transition: MemoryTierTransition,
        *,
        options: MemoryBundleImportOptions,
        record_id_map: dict[str, str],
    ) -> MemoryTierTransition | None:
        record_id = record_id_map.get(transition.record_id, transition.record_id)
        if not record_id:
            return None
        transition_id = (
            _new_id("mtt")
            if options.id_mode == "regenerate"
            else transition.transition_id
        )
        return replace(
            transition,
            transition_id=transition_id,
            record_id=record_id,
            scope=_rewrite_scope(transition.scope, options.scope_rewrites),  # type: ignore[arg-type]
        )

    def _import_candidate_mode(
        self,
        *,
        rewritten_records: list[MemoryRecord],
        bundle_id: str,
        source_instance: Any,
        imported_at: str,
        snapshot: MemoryBundleSnapshot,
        options: MemoryBundleImportOptions,
    ) -> MemoryBundleImportResult:
        staged_candidates = 0
        for record in rewritten_records:
            meta = dict(record.meta or {})
            meta.update(
                {
                    "import_source_record_id": record.id,
                    "import_source_scope": record.scope,
                }
            )
            candidate = MemoryCandidate(
                candidate_id=_new_id("cand"),
                session_id=f"import:{bundle_id or 'bundle'}",
                proposed_scope=record.scope,  # type: ignore[arg-type]
                type=record.type,
                content=record.content,
                tags=list(record.tags or []),
                entities=list(record.entities or []),
                source="imported",
                confidence=float(record.confidence or 0.5),
                evidence_refs=list(record.evidence_refs or []),
                key=record.key,
                title=record.title,
                meta=meta,
                created_at=imported_at,
                updated_at=imported_at,
            )
            if not options.dry_run:
                self._service.candidate_put(candidate)
            staged_candidates += 1
        skipped_sections = [
            name
            for name, payload in (
                ("candidates", snapshot.candidates),
                ("relations", snapshot.relations),
                ("tier_transitions", snapshot.tier_transitions),
                ("provenance_traces", snapshot.provenance_traces),
            )
            if payload
        ]
        return MemoryBundleImportResult(
            applied=not options.dry_run,
            trust_mode=options.trust_mode,
            conflict_mode=options.conflict_mode,
            id_mode="generated_candidates",
            staged_candidates=staged_candidates,
            skipped_sections=skipped_sections,
            rewrites=dict(options.scope_rewrites),
        )

    def _import_direct_mode(
        self,
        *,
        snapshot: MemoryBundleSnapshot,
        rewritten_records: list[MemoryRecord],
        record_id_map: dict[str, str],
        bundle_id: str,
        source_instance: Any,
        imported_at: str,
        options: MemoryBundleImportOptions,
    ) -> MemoryBundleImportResult:
        conflicts: list[dict[str, Any]] = []
        imported_records = 0
        skipped_records = 0

        for source_record, record in zip(
            snapshot.records, rewritten_records, strict=False
        ):
            existing_exact = self._service._store.get(record.id)  # noqa: SLF001
            if existing_exact is not None and _record_equal(
                existing_exact,
                record,
                ignore_id=False,
            ):
                record_id_map[source_record.id] = existing_exact.id
                skipped_records += 1
                continue

            existing_key = None
            if record.key:
                existing_key = self._service.find_record_by_normalized_key(
                    scope=record.scope,
                    record_type=record.type,
                    normalized_key=record.key,
                )
                if existing_key is not None and _record_equal(
                    existing_key,
                    record,
                    ignore_id=True,
                ):
                    record_id_map[source_record.id] = existing_key.id
                    skipped_records += 1
                    continue

            conflict_reason = None
            conflict_target = None
            if existing_exact is not None:
                conflict_reason = "exact_id_conflict"
                conflict_target = existing_exact.id
            elif existing_key is not None and existing_key.id != record.id:
                conflict_reason = "normalized_key_conflict"
                conflict_target = existing_key.id

            if conflict_reason is not None and options.conflict_mode == "error":
                conflicts.append(
                    {
                        "record_id": source_record.id,
                        "target_id": conflict_target,
                        "reason": conflict_reason,
                    }
                )
                continue
            if conflict_reason is not None and options.conflict_mode == "skip":
                if conflict_target:
                    record_id_map[source_record.id] = conflict_target
                skipped_records += 1
                continue

            if not options.dry_run:
                self._service._store.put(record)  # noqa: SLF001
                if conflict_reason == "normalized_key_conflict" and conflict_target:
                    updated = self._service.supersede_by_contradiction(
                        conflict_target,
                        record.id,
                        reason="bundle_import",
                    )
                    record_id_map[source_record.id] = updated.id
                else:
                    record_id_map[source_record.id] = record.id
            else:
                record_id_map[source_record.id] = record.id
            imported_records += 1

        if conflicts:
            return MemoryBundleImportResult(
                applied=False,
                trust_mode=options.trust_mode,
                conflict_mode=options.conflict_mode,
                id_mode=options.id_mode,
                imported_records=imported_records,
                skipped_records=skipped_records,
                conflicts=conflicts,
                rewrites=dict(options.scope_rewrites),
            )

        imported_candidates = 0
        for candidate in snapshot.candidates:
            rewritten = self._rewrite_candidate(
                candidate,
                options=options,
                bundle_id=bundle_id,
                source_instance=source_instance,
                imported_at=imported_at,
            )
            existing = self._service._store.candidate_get(rewritten.candidate_id)  # noqa: SLF001
            if existing is not None and _candidate_equal(
                existing, rewritten, ignore_id=False
            ):
                continue
            if not options.dry_run:
                self._service.candidate_put(rewritten)
            imported_candidates += 1

        imported_relations = 0
        for relation in snapshot.relations:
            rewritten_relation = self._rewrite_relation(
                relation,
                options=options,
                record_id_map=record_id_map,
            )
            if rewritten_relation is None:
                continue
            if not options.dry_run:
                self._service._store.put_relation(rewritten_relation)  # noqa: SLF001
            imported_relations += 1

        imported_tier_transitions = 0
        for transition in snapshot.tier_transitions:
            rewritten_transition = self._rewrite_tier_transition(
                transition,
                options=options,
                record_id_map=record_id_map,
            )
            if rewritten_transition is None:
                continue
            if not options.dry_run:
                self._service.put_tier_transition(rewritten_transition)
            imported_tier_transitions += 1

        imported_provenance_traces = 0
        if snapshot.provenance_traces:
            from openminion.modules.memory.runtime.provenance import (
                default_provenance_recorder,
            )

            recorder = default_provenance_recorder()
            for trace in snapshot.provenance_traces:
                if not options.dry_run:
                    recorder.record_turn_trace(trace)
                imported_provenance_traces += 1

        return MemoryBundleImportResult(
            applied=not options.dry_run,
            trust_mode=options.trust_mode,
            conflict_mode=options.conflict_mode,
            id_mode=options.id_mode,
            imported_records=imported_records,
            imported_candidates=imported_candidates,
            imported_relations=imported_relations,
            imported_tier_transitions=imported_tier_transitions,
            imported_provenance_traces=imported_provenance_traces,
            skipped_records=skipped_records,
            conflicts=conflicts,
            rewrites=dict(options.scope_rewrites),
        )


__all__ = ["MemoryMerger"]
