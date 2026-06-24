from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import uuid
from typing import Any, Literal, TypeVar

from openminion.modules.memory.constants import (
    MEMORY_CANDIDATE_STATUS_PROMOTED,
    PROMOTABLE_MEMORY_CANDIDATE_STATUSES,
)
from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryRelation,
    MemoryRelationType,
    MemoryRecord,
    MemoryTierTransition,
    MemoryType,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    SearchQueryOptions,
)
from openminion.modules.memory.storage.capabilities import (
    BackendCapabilities,
    CapabilityMemoryStore,
)

from openminion.base.time import utc_now_iso as _utc_now_iso
from ..errors import InvalidArgumentError, NotFoundError, PromotionDeniedError

T = TypeVar("T")


def _apply_limit(rows: list[T], limit: int | None) -> list[T]:
    if limit is None:
        return rows
    return rows[: max(1, int(limit))]


def _text_for_search(record: MemoryRecord) -> str:
    chunks = [str(record.title or ""), str(record.key or "")]
    if isinstance(record.content, dict):
        chunks.extend(str(value) for value in record.content.values())
    else:
        chunks.append(str(record.content or ""))
    chunks.extend(str(tag) for tag in record.tags)
    chunks.extend(str(entity) for entity in record.entities)
    return " ".join(chunks).strip().lower()


def _is_temporally_current(
    record: MemoryRecord, *, now: datetime | None = None
) -> bool:
    if record.valid_to is None:
        return True
    target = now or datetime.now(timezone.utc)
    return not record.is_invalidated_at(target)


class InMemoryRecordStore:
    def __init__(self) -> None:
        self._records: dict[str, MemoryRecord] = {}
        self._candidates: dict[str, MemoryCandidate] = {}
        self._relations: dict[str, MemoryRelation] = {}
        self._tier_transitions: dict[str, MemoryTierTransition] = {}

    def put(self, record: MemoryRecord) -> str:
        self._records[record.id] = record
        return record.id

    def upsert(
        self, scope: str, type: MemoryType, key: str, record_patch: dict[str, Any]
    ) -> MemoryRecord:
        active = [
            item
            for item in self._records.values()
            if item.scope == scope
            and item.type == type
            and item.key == key
            and not item.is_deleted
            and item.superseded_by_id is None
        ]
        if active:
            base = active[-1]
            content = record_patch.get("content", base.content)
            if isinstance(base.content, dict) and isinstance(
                record_patch.get("content"), dict
            ):
                merged = dict(base.content)
                merged.update(record_patch.get("content") or {})
                content = merged
            updated = replace(
                base,
                id=f"mem_{uuid.uuid4().hex[:12]}",
                content=content,
                tags=list(record_patch.get("tags", base.tags)),
                entities=list(record_patch.get("entities", base.entities)),
                source=str(record_patch.get("source", base.source or "")),
                confidence=float(
                    record_patch.get("confidence", base.confidence or 0.0)
                ),
                meta=dict(record_patch.get("meta", base.meta)),
                last_hit_at=base.last_hit_at,
                tier=base.tier,
                access_count=base.access_count,
                updated_at=_utc_now_iso(),
                created_at=_utc_now_iso(),
                supersedes_id=base.id,
                superseded_by_id=None,
                supersession_reason=None,
                is_deleted=False,
            )
            self._records[base.id] = replace(
                base,
                superseded_by_id=updated.id,
                supersession_reason="keyed_upsert",
                valid_to=updated.created_at,
                is_deleted=True,
                updated_at=_utc_now_iso(),
            )
            self._records[updated.id] = updated
            return updated

        now = _utc_now_iso()
        created = MemoryRecord(
            id=f"mem_{uuid.uuid4().hex[:12]}",
            scope=scope,  # type: ignore[arg-type]
            type=type,
            key=key,
            title=str(record_patch.get("title", "") or ""),
            content=record_patch.get("content", {}),
            tags=list(record_patch.get("tags", [])),
            entities=list(record_patch.get("entities", [])),
            source=str(record_patch.get("source", "agent_inferred")),
            confidence=float(record_patch.get("confidence", 0.5)),
            evidence_refs=list(record_patch.get("evidence_refs", [])),
            meta=dict(record_patch.get("meta", {})),
            goal_id=record_patch.get("goal_id"),
            expires_at=record_patch.get("expires_at"),
            created_at=now,
            updated_at=now,
            last_hit_at=None,
            tier="working",
            access_count=0,
            supersedes_id=None,
            superseded_by_id=None,
            supersession_reason=None,
            is_deleted=False,
        )
        self._records[created.id] = created
        return created

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    def delete(
        self,
        record_id: str,
        *,
        reason: str | None = None,
        deleted_at: str | None = None,
    ) -> None:
        """Runtime helper."""

        current = self._records.get(record_id)
        if current is None:
            return
        now = _utc_now_iso()
        audit_supplied = reason is not None
        self._records[record_id] = replace(
            current,
            is_deleted=True,
            updated_at=now,
            deleted_at=(
                (deleted_at if deleted_at is not None else now)
                if audit_supplied
                else current.deleted_at
            ),
            deleted_reason=(reason if audit_supplied else current.deleted_reason),
        )

    def tombstone(self, scope: str, type: MemoryType, key: str) -> None:
        for record_id, record in list(self._records.items()):
            if (
                record.scope == scope
                and record.type == type
                and record.key == key
                and not record.is_deleted
            ):
                self._records[record_id] = replace(
                    record, is_deleted=True, updated_at=_utc_now_iso()
                )

    def list(self, options: ListQueryOptions) -> list[MemoryRecord]:
        allowed_types = {str(t) for t in options.types} if options.types else None
        allowed_tiers = {str(t) for t in options.tiers} if options.tiers else None
        rows = [
            item
            for item in self._records.values()
            if (
                not item.is_deleted
                or (options.include_invalidated and item.superseded_by_id is not None)
            )
            and (options.include_invalidated or _is_temporally_current(item))
            and (not options.scopes or item.scope in options.scopes)
            and (allowed_types is None or str(item.type) in allowed_types)
            and (allowed_tiers is None or str(item.tier) in allowed_tiers)
        ]
        if options.order_by and str(options.order_by.value) == "updated_at_asc":
            rows.sort(key=lambda item: item.updated_at)
        else:
            rows.sort(key=lambda item: item.updated_at, reverse=True)
        offset = max(0, int(options.offset or 0))
        return _apply_limit(rows[offset:], options.limit)

    def list_scopes(self) -> list[str]:
        return sorted(
            {
                str(item.scope)
                for item in self._records.values()
                if not item.is_deleted and str(item.scope).strip()
            }
        )

    def list_records_by_goal_id(
        self,
        goal_id: str,
        *,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        normalized_goal_id = str(goal_id or "").strip()
        if not normalized_goal_id:
            return []
        rows = [
            record
            for record in self._records.values()
            if not record.is_deleted
            and str(getattr(record, "goal_id", "") or "").strip() == normalized_goal_id
            and (not scopes or record.scope in scopes)
        ]
        rows.sort(key=lambda item: item.updated_at, reverse=True)
        return _apply_limit(rows, limit)

    def touch_last_hit(self, record_id: str) -> None:
        current = self._records.get(record_id)
        if current is None:
            raise NotFoundError(f"record not found: {record_id}")
        self._records[record_id] = replace(
            current,
            last_hit_at=_utc_now_iso(),
            access_count=int(current.access_count) + 1,
        )

    def apply_outcome_feedback(
        self,
        record_ids: list[str],
        *,
        outcome: Literal["success", "failed", "timeout"],
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        normalized_ids: list[str] = []
        seen: set[str] = set()
        for record_id in record_ids:
            normalized = str(record_id or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_ids.append(normalized)
        if not normalized_ids:
            return 0
        updated = 0
        now_iso = str(observed_at or "").strip() or _utc_now_iso()
        feedback_delta_value = float(feedback_delta)
        for record_id in normalized_ids:
            current = self._records.get(record_id)
            if current is None or current.is_deleted or current.superseded_by_id:
                continue
            meta = dict(current.meta or {})
            try:
                existing_feedback = float(meta.get("feedback_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                existing_feedback = 0.0
            meta["feedback_score"] = max(
                0.0,
                min(1.0, existing_feedback + feedback_delta_value),
            )
            if outcome == "success":
                meta["outcome_success_count"] = (
                    int(meta.get("outcome_success_count", 0) or 0) + 1
                )
                meta.setdefault(
                    "outcome_failure_count",
                    int(meta.get("outcome_failure_count", 0) or 0),
                )
            else:
                meta["outcome_failure_count"] = (
                    int(meta.get("outcome_failure_count", 0) or 0) + 1
                )
                meta.setdefault(
                    "outcome_success_count",
                    int(meta.get("outcome_success_count", 0) or 0),
                )
            meta["last_outcome_at"] = now_iso
            meta["last_outcome_status"] = outcome
            meta["last_outcome_command_id"] = str(command_id or "").strip()
            self._records[record_id] = replace(
                current,
                meta=meta,
                updated_at=now_iso,
            )
            updated += 1
        return updated

    def put_relation(self, relation: MemoryRelation) -> str:
        if relation.source_record_id not in self._records:
            raise NotFoundError(f"record not found: {relation.source_record_id}")
        if relation.target_record_id not in self._records:
            raise NotFoundError(f"record not found: {relation.target_record_id}")
        self._relations[relation.relation_id] = relation
        return relation.relation_id

    def list_relations(
        self,
        record_id: str,
        *,
        relation_types: list[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRelation]:
        rows = [
            relation
            for relation in self._relations.values()
            if relation.source_record_id == record_id
            or relation.target_record_id == record_id
        ]
        if relation_types:
            allowed = {str(item) for item in relation_types}
            rows = [item for item in rows if str(item.relation_type) in allowed]
        rows.sort(key=lambda item: (item.created_at, item.relation_id), reverse=True)
        return _apply_limit(rows, limit)

    def get_related_records(
        self,
        record_id: str,
        scopes: list[str],
        *,
        relation_types: list[MemoryRelationType] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        scoped = {str(scope) for scope in scopes if str(scope).strip()}
        related_ids: list[str] = []
        seen: set[str] = set()
        for relation in self.list_relations(
            record_id,
            relation_types=relation_types,
            limit=None,
        ):
            related_id = (
                relation.target_record_id
                if relation.source_record_id == record_id
                else relation.source_record_id
            )
            if related_id in seen:
                continue
            related = self._records.get(related_id)
            if related is None or related.is_deleted or related.superseded_by_id:
                continue
            if scoped and related.scope not in scoped:
                continue
            seen.add(related_id)
            related_ids.append(related_id)
        rows = [self._records[item] for item in related_ids]
        return _apply_limit(rows, limit)

    def candidate_put(self, candidate: MemoryCandidate) -> str:
        now = _utc_now_iso()
        stored = replace(
            candidate,
            created_at=candidate.created_at or now,
            updated_at=candidate.updated_at or now,
        )
        self._candidates[candidate.candidate_id] = stored
        return candidate.candidate_id

    def candidate_get(self, candidate_id: str) -> MemoryCandidate | None:
        return self._candidates.get(candidate_id)

    def candidate_list(self, options: CandidateListOptions) -> list[MemoryCandidate]:
        rows = [
            item
            for item in self._candidates.values()
            if (options.session_id is None or item.session_id == options.session_id)
            and (
                options.proposed_scope is None
                or item.proposed_scope == options.proposed_scope
            )
            and (options.status is None or str(item.status) == str(options.status))
        ]
        rows.sort(
            key=lambda item: (
                str(item.created_at or ""),
                item.candidate_id,
            )
        )
        return _apply_limit(rows, options.limit)

    def candidate_update(
        self, candidate_id: str, patch: dict[str, Any]
    ) -> MemoryCandidate:
        current = self._candidates.get(candidate_id)
        if current is None:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        updated = replace(
            current,
            session_id=str(patch.get("session_id", current.session_id)),
            status=str(patch.get("status", current.status)),
            proposed_scope=str(patch.get("proposed_scope", current.proposed_scope)),
            title=(
                str(patch.get("title"))
                if patch.get("title") is not None
                else current.title
            ),
            content=patch.get("content", current.content),
            tags=list(patch.get("tags", current.tags)),
            entities=list(patch.get("entities", current.entities)),
            source=str(patch.get("source", current.source)),
            confidence=float(patch.get("confidence", current.confidence)),
            key=str(patch.get("key")) if patch.get("key") is not None else current.key,
            review=patch.get("review", current.review),
            meta=dict(patch.get("meta", current.meta)),
            updated_at=str(patch.get("updated_at", _utc_now_iso())),
        )
        self._candidates[candidate_id] = updated
        return updated

    def promote_candidate(self, candidate_id: str, target_scope: str) -> MemoryRecord:
        current = self._candidates.get(candidate_id)
        if current is None:
            raise NotFoundError(f"candidate not found: {candidate_id}")
        if str(current.status) not in PROMOTABLE_MEMORY_CANDIDATE_STATUSES:
            raise PromotionDeniedError(
                f"Candidate {candidate_id} is not approved for promotion"
            )
        now = _utc_now_iso()
        new_id = f"mem_{uuid.uuid4().hex[:12]}"
        record_key = current.key or current.candidate_id
        supersedes_existing_id: str | None = None
        if current.key:
            for existing_id, existing in list(self._records.items()):
                if existing.is_deleted or existing.superseded_by_id:
                    continue
                if (
                    str(existing.scope) == str(target_scope)
                    and str(existing.type) == str(current.type)
                    and str(getattr(existing, "key", "") or "")
                    == str(current.key or "")
                ):
                    supersedes_existing_id = existing_id
                    break
        record = MemoryRecord(
            id=new_id,
            scope=target_scope,  # type: ignore[arg-type]
            type=current.type,
            title=current.title,
            content=current.content,
            tags=list(current.tags),
            entities=list(current.entities),
            source=current.source,
            confidence=current.confidence,
            evidence_refs=list(current.evidence_refs),
            created_at=now,
            updated_at=now,
            key=record_key,
            last_hit_at=None,
            tier="working",
            access_count=0,
            supersedes_id=supersedes_existing_id,
            supersession_reason=None,
        )
        self._records[record.id] = record
        if supersedes_existing_id is not None:
            old = self._records[supersedes_existing_id]
            self._records[supersedes_existing_id] = replace(
                old,
                superseded_by_id=new_id,
                supersession_reason="candidate_promotion",
                valid_to=record.created_at,
                is_deleted=True,
                updated_at=now,
            )
        self._candidates[candidate_id] = replace(
            current,
            status=MEMORY_CANDIDATE_STATUS_PROMOTED,
        )
        return record

    def supersede_by_contradiction(
        self, old_record_id: str, new_record_id: str, reason: str = ""
    ) -> MemoryRecord:
        old_record = self._records.get(old_record_id)
        new_record = self._records.get(new_record_id)
        if old_record is None:
            raise NotFoundError(f"record not found: {old_record_id}")
        if new_record is None:
            raise NotFoundError(f"record not found: {new_record_id}")
        if old_record_id == new_record_id:
            raise InvalidArgumentError("old and new records must differ")
        now = _utc_now_iso()
        self._records[old_record_id] = replace(
            old_record,
            superseded_by_id=new_record_id,
            supersession_reason=reason or None,
            valid_to=new_record.created_at or now,
            is_deleted=True,
            updated_at=now,
        )
        updated_new = replace(
            new_record,
            key=old_record.key or new_record.key,
            supersedes_id=old_record_id,
            supersession_reason=None,
            is_deleted=False,
            updated_at=now,
        )
        self._records[new_record_id] = updated_new
        return updated_new

    def invalidate(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> MemoryRecord:
        del reason
        record = self._records.get(record_id)
        if record is None:
            raise NotFoundError(f"record not found: {record_id}")
        updated = replace(record, valid_to=valid_to, updated_at=_utc_now_iso())
        self._records[record_id] = updated
        return updated

    def history(self, scope: str, type: MemoryType, key: str) -> list[MemoryRecord]:
        rows = [
            item
            for item in self._records.values()
            if item.scope == scope and item.type == type and item.key == key
        ]
        rows.sort(key=lambda item: item.updated_at, reverse=True)
        return rows

    def transition_tier(
        self,
        record_id: str,
        *,
        to_tier: str,
        transition_reason: str,
        transition_at: str,
        meta: dict[str, Any] | None = None,
    ) -> MemoryTierTransition:
        current = self._records.get(record_id)
        if current is None:
            raise NotFoundError(f"record not found: {record_id}")
        if str(current.tier) == str(to_tier):
            raise InvalidArgumentError("from_tier and to_tier must differ")
        transition = MemoryTierTransition(
            transition_id=f"mtt_{uuid.uuid4().hex[:12]}",
            record_id=record_id,
            scope=current.scope,
            record_type=current.type,
            from_tier=current.tier,
            to_tier=to_tier,  # type: ignore[arg-type]
            transition_reason=transition_reason,  # type: ignore[arg-type]
            transition_at=transition_at,
            access_count=int(current.access_count),
            meta=dict(meta or {}),
        )
        self._records[record_id] = replace(
            current,
            tier=to_tier,
            updated_at=transition_at,
        )
        self._tier_transitions[transition.transition_id] = transition
        return transition

    def list_tier_transitions(
        self,
        *,
        record_id: str | None = None,
        scopes: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryTierTransition]:
        rows = list(self._tier_transitions.values())
        if record_id:
            rows = [item for item in rows if item.record_id == record_id]
        if scopes:
            allowed = {str(scope) for scope in scopes if str(scope).strip()}
            rows = [item for item in rows if item.scope in allowed]
        rows.sort(
            key=lambda item: (item.transition_at, item.transition_id), reverse=True
        )
        return _apply_limit(rows, limit)

    def put_tier_transition(self, transition: MemoryTierTransition) -> str:
        if transition.record_id not in self._records:
            raise NotFoundError(f"record not found: {transition.record_id}")
        self._tier_transitions[transition.transition_id] = transition
        return transition.transition_id


class InMemorySearchIndex:
    def __init__(self, records: InMemoryRecordStore) -> None:
        self._records = records

    def search(self, options: SearchQueryOptions) -> list[MemoryRecord]:
        query = str(options.query or "").strip().lower()
        rows = self._records.list(
            ListQueryOptions(
                scopes=list(options.scopes),
                types=options.types,
                tiers=options.tiers,
                limit=None,
                offset=0,
                order_by=None,
            )
        )
        if query:
            rows = [item for item in rows if query in _text_for_search(item)]
        return _apply_limit(rows, options.limit)

    def retrieve_by_entities(
        self,
        entities: list[str],
        scopes: list[str],
        *,
        types: list[MemoryType] | None = None,
        tiers: list[str] | None = None,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        requested = {
            str(item).strip().lower() for item in entities if str(item).strip()
        }
        rows = self._records.list(
            ListQueryOptions(
                scopes=list(scopes),
                types=types,
                tiers=tiers,
                limit=None,
                offset=0,
                order_by=None,
            )
        )
        if requested:
            filtered: list[MemoryRecord] = []
            for item in rows:
                pool = {str(entity).strip().lower() for entity in item.entities}
                text_tokens = set(_text_for_search(item).split())
                if pool.intersection(requested) or text_tokens.intersection(requested):
                    filtered.append(item)
            rows = filtered
        return _apply_limit(rows, limit)


class InMemoryMemoryStore(CapabilityMemoryStore):
    """Ready-to-use in-memory backend for tests and parity checks."""

    def __init__(self) -> None:
        records = InMemoryRecordStore()
        super().__init__(
            records=records,
            search=InMemorySearchIndex(records),
            capabilities=BackendCapabilities(
                supports_full_text=True,
                supports_vector_search=False,
                supports_candidate_workflow=True,
                supports_history=True,
                supports_capsule_cache=False,
                supports_transactions=False,
            ),
        )


__all__ = [
    "InMemoryMemoryStore",
]
