"""Built-in compatibility backend that wraps the current durable-memory store."""

from typing import Any, Callable, Iterable, cast

from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.storage.base import MemoryStore

from .interfaces import (
    KNOWLEDGE_BACKEND_VERSION,
    KnowledgeBackend,
    MemoryCandidateLike,
    MemoryRecordLike,
    MemoryRelationLike,
    MemoryTierTransitionLike,
    MemoryTypeLike,
)

ExportSnapshotFn = Callable[[MemoryBundleExportOptions], MemoryBundleSnapshot]
ImportSnapshotFn = Callable[
    [MemoryBundleSnapshot, MemoryBundleImportOptions],
    MemoryBundleImportResult,
]


class BuiltinKnowledgeBackend(KnowledgeBackend):
    """Thin adapter from the current ``MemoryStore`` owner to ``KnowledgeBackend``."""

    contract_version = KNOWLEDGE_BACKEND_VERSION

    def __init__(
        self,
        store: MemoryStore,
        *,
        export_snapshot_fn: ExportSnapshotFn | None = None,
        import_snapshot_fn: ImportSnapshotFn | None = None,
    ) -> None:
        self.store = store
        self._export_snapshot_fn = export_snapshot_fn
        self._import_snapshot_fn = import_snapshot_fn

    def put_record(self, record: MemoryRecordLike) -> str:
        return self.store.put(record)

    def upsert_record(
        self,
        scope: str,
        type: MemoryTypeLike,
        key: str,
        record_patch: dict[str, Any],
    ) -> MemoryRecordLike:
        return self.store.upsert(scope, type, key, record_patch)

    def get_record(self, record_id: str) -> MemoryRecordLike | None:
        return self.store.get(record_id)

    def list_records(self, options):
        return self.store.list(options)

    def search_records(self, options):
        return self.store.search(options)

    def invalidate_record(
        self,
        record_id: str,
        *,
        valid_to: str,
        reason: str,
    ) -> MemoryRecordLike:
        return self.store.invalidate(record_id, valid_to=valid_to, reason=reason)

    def supersede_record(
        self,
        old_record_id: str,
        new_record_id: str,
        reason: str = "",
    ) -> MemoryRecordLike:
        return self.store.supersede_by_contradiction(
            old_record_id,
            new_record_id,
            reason,
        )

    def put_relation(self, relation: MemoryRelationLike) -> str:
        return self.store.put_relation(relation)

    def list_relations(self, record_id: str, *, relation_types=None, limit=None):
        return self.store.list_relations(
            record_id,
            relation_types=relation_types,
            limit=limit,
        )

    def get_related_records(
        self,
        record_id: str,
        scopes: list[str],
        *,
        relation_types=None,
        limit=None,
    ) -> list[MemoryRecordLike]:
        return self.store.get_related_records(
            record_id,
            scopes,
            relation_types=relation_types,
            limit=limit,
        )

    def put_candidate(self, candidate: MemoryCandidateLike) -> str:
        return self.store.candidate_put(candidate)

    def get_candidate(self, candidate_id: str) -> MemoryCandidateLike | None:
        return self.store.candidate_get(candidate_id)

    def list_candidates(self, options):
        return self.store.candidate_list(options)

    def update_candidate(self, candidate_id: str, patch: dict[str, Any]):
        return self.store.candidate_update(candidate_id, patch)

    def promote_candidate(self, candidate_id: str, target_scope: str):
        return self.store.promote_candidate(candidate_id, target_scope)

    def list_tier_transitions(self, *, record_id=None, scopes=None, limit=None):
        return self.store.list_tier_transitions(
            record_id=record_id,
            scopes=scopes,
            limit=limit,
        )

    def put_tier_transition(self, transition: MemoryTierTransitionLike) -> str:
        return self.store.put_tier_transition(transition)

    def history(self, scope: str, type: MemoryTypeLike, key: str):
        return self.store.history(scope, type, key)

    def export_snapshot(
        self,
        options: MemoryBundleExportOptions,
    ) -> MemoryBundleSnapshot:
        if self._export_snapshot_fn is None:
            raise InvalidArgumentError(
                "builtin backend export_snapshot is not configured yet"
            )
        return self._export_snapshot_fn(options)

    def import_snapshot(
        self,
        snapshot: MemoryBundleSnapshot,
        options: MemoryBundleImportOptions,
    ) -> MemoryBundleImportResult:
        if self._import_snapshot_fn is None:
            raise InvalidArgumentError(
                "builtin backend import_snapshot is not configured yet"
            )
        return self._import_snapshot_fn(snapshot, options)

    def delete_record(self, record_id: str) -> None:
        self.store.delete(record_id)

    def tombstone_record(self, scope: str, type: MemoryTypeLike, key: str) -> None:
        self.store.tombstone(scope, type, key)

    def list_scopes(self) -> list[str]:
        return self.store.list_scopes()

    def touch_last_hit(self, record_id: str) -> None:
        self.store.touch_last_hit(record_id)

    def apply_outcome_feedback(
        self,
        record_ids: list[str],
        *,
        outcome: str,
        command_id: str,
        observed_at: str,
        feedback_delta: float,
    ) -> int:
        return self.store.apply_outcome_feedback(
            record_ids,
            outcome=cast(Any, outcome),
            command_id=command_id,
            observed_at=observed_at,
            feedback_delta=feedback_delta,
        )

    def retrieve_by_entities(
        self,
        entities: list[str],
        scopes: list[str],
        *,
        types=None,
        tiers=None,
        limit=None,
    ) -> list[MemoryRecordLike]:
        return self.store.retrieve_by_entities(
            entities,
            scopes,
            types=types,
            tiers=tiers,
            limit=limit,
        )

    def transition_tier(
        self,
        record_id: str,
        *,
        to_tier,
        transition_reason,
        transition_at: str,
        meta: dict[str, Any] | None = None,
    ) -> MemoryTierTransitionLike:
        return self.store.transition_tier(
            record_id,
            to_tier=to_tier,
            transition_reason=transition_reason,
            transition_at=transition_at,
            meta=meta,
        )

    def iter_all_records(self) -> Iterable[MemoryRecordLike]:
        if hasattr(self.store, "iter_all_records"):
            yield from cast(Any, self.store).iter_all_records()
            return
        if hasattr(self.store, "list_all"):
            yield from cast(Any, self.store).list_all()
            return
        raise NotImplementedError("store does not expose iter_all_records or list_all")

    def list_all(self) -> list[MemoryRecordLike]:
        if hasattr(self.store, "list_all"):
            return list(cast(Any, self.store).list_all())
        return list(self.iter_all_records())


class BackendMemoryStoreAdapter(MemoryStore):
    """Backendmemorystoreadapter contract."""

    def __init__(self, backend: KnowledgeBackend) -> None:
        self._backend = backend

    def put(self, record):
        return self._backend.put_record(record)

    def upsert(self, scope, type, key, record_patch):
        return self._backend.upsert_record(scope, type, key, record_patch)

    def get(self, record_id):
        return self._backend.get_record(record_id)

    def delete(self, record_id):
        return _call_backend_extra(self._backend, "delete_record", record_id)

    def invalidate(self, record_id, *, valid_to, reason):
        return self._backend.invalidate_record(
            record_id, valid_to=valid_to, reason=reason
        )

    def tombstone(self, scope, type, key):
        return _call_backend_extra(self._backend, "tombstone_record", scope, type, key)

    def list(self, options):
        return self._backend.list_records(options)

    def search(self, options):
        return self._backend.search_records(options)

    def list_scopes(self):
        return _call_backend_extra(self._backend, "list_scopes")

    def touch_last_hit(self, record_id):
        return _call_backend_extra(self._backend, "touch_last_hit", record_id)

    def apply_outcome_feedback(
        self,
        record_ids,
        *,
        outcome,
        command_id,
        observed_at,
        feedback_delta,
    ):
        return _call_backend_extra(
            self._backend,
            "apply_outcome_feedback",
            record_ids,
            outcome=outcome,
            command_id=command_id,
            observed_at=observed_at,
            feedback_delta=feedback_delta,
        )

    def retrieve_by_entities(
        self, entities, scopes, *, types=None, tiers=None, limit=None
    ):
        return _call_backend_extra(
            self._backend,
            "retrieve_by_entities",
            entities,
            scopes,
            types=types,
            tiers=tiers,
            limit=limit,
        )

    def list_records_by_goal_id(self, goal_id, *, scopes=None, limit=None):
        return _call_backend_extra(
            self._backend,
            "list_records_by_goal_id",
            goal_id,
            scopes=scopes,
            limit=limit,
        )

    def transition_tier(
        self,
        record_id,
        *,
        to_tier,
        transition_reason,
        transition_at,
        meta=None,
    ):
        return _call_backend_extra(
            self._backend,
            "transition_tier",
            record_id,
            to_tier=to_tier,
            transition_reason=transition_reason,
            transition_at=transition_at,
            meta=meta,
        )

    def list_tier_transitions(self, *, record_id=None, scopes=None, limit=None):
        return self._backend.list_tier_transitions(
            record_id=record_id,
            scopes=scopes,
            limit=limit,
        )

    def put_tier_transition(self, transition):
        return self._backend.put_tier_transition(transition)

    def put_relation(self, relation):
        return self._backend.put_relation(relation)

    def list_relations(self, record_id, *, relation_types=None, limit=None):
        return self._backend.list_relations(
            record_id,
            relation_types=relation_types,
            limit=limit,
        )

    def get_related_records(
        self, record_id, scopes, *, relation_types=None, limit=None
    ):
        return self._backend.get_related_records(
            record_id,
            scopes,
            relation_types=relation_types,
            limit=limit,
        )

    def candidate_put(self, candidate):
        return self._backend.put_candidate(candidate)

    def candidate_get(self, candidate_id):
        return self._backend.get_candidate(candidate_id)

    def candidate_list(self, options):
        return self._backend.list_candidates(options)

    def candidate_update(self, candidate_id, patch):
        return self._backend.update_candidate(candidate_id, patch)

    def promote_candidate(self, candidate_id, target_scope):
        return self._backend.promote_candidate(candidate_id, target_scope)

    def supersede_by_contradiction(self, old_record_id, new_record_id, reason=""):
        return self._backend.supersede_record(old_record_id, new_record_id, reason)

    def history(self, scope, type, key):
        return self._backend.history(scope, type, key)

    def iter_all_records(self):
        return _call_backend_extra(self._backend, "iter_all_records")

    def list_all(self):
        return _call_backend_extra(self._backend, "list_all")


def adapt_backend_to_store(backend: KnowledgeBackend) -> MemoryStore:
    if isinstance(backend, BuiltinKnowledgeBackend):
        return backend.store
    return BackendMemoryStoreAdapter(backend)


def _call_backend_extra(
    backend: KnowledgeBackend, method: str, *args: Any, **kwargs: Any
):
    if not callable(target := getattr(backend, method, None)):
        raise NotImplementedError(
            f"backend {type(backend).__name__} does not expose {method}()"
        )
    return target(*args, **kwargs)


__all__ = [
    "BackendMemoryStoreAdapter",
    "BuiltinKnowledgeBackend",
    "adapt_backend_to_store",
]
