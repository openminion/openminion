from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleImportResult,
    MemoryBundleSnapshot,
)

from .interfaces import KNOWLEDGE_BACKEND_VERSION, KnowledgeBackend

_DISABLED_WRITE = (
    "memory.backend.provider='none' disables durable-memory writes; "
    "use the disabled gateway path or choose a writable backend"
)


class NoneKnowledgeBackend(KnowledgeBackend):
    """Backend that exposes empty reads and explicit disabled-write diagnostics."""

    contract_version = KNOWLEDGE_BACKEND_VERSION

    def put_record(self, record):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def upsert_record(self, scope, type, key, record_patch):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def get_record(self, record_id):
        return None

    def list_records(self, options):
        return []

    def search_records(self, options):
        return []

    def invalidate_record(self, record_id, *, valid_to, reason):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def supersede_record(self, old_record_id, new_record_id, reason=""):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def put_relation(self, relation):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def list_relations(self, record_id, *, relation_types=None, limit=None):
        return []

    def get_related_records(
        self, record_id, scopes, *, relation_types=None, limit=None
    ):
        return []

    def put_candidate(self, candidate):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def get_candidate(self, candidate_id):
        return None

    def list_candidates(self, options):
        return []

    def update_candidate(self, candidate_id, patch):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def promote_candidate(self, candidate_id, target_scope):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def list_tier_transitions(self, *, record_id=None, scopes=None, limit=None):
        return []

    def put_tier_transition(self, transition):
        raise InvalidArgumentError(_DISABLED_WRITE)

    def history(self, scope, type, key):
        return []

    def export_snapshot(
        self, options: MemoryBundleExportOptions
    ) -> MemoryBundleSnapshot:
        return MemoryBundleSnapshot(manifest={"backend": "none"}, records=[])

    def import_snapshot(
        self,
        snapshot: MemoryBundleSnapshot,
        options: MemoryBundleImportOptions,
    ) -> MemoryBundleImportResult:
        return MemoryBundleImportResult(
            applied=False,
            trust_mode=options.trust_mode,
            conflict_mode=options.conflict_mode,
            id_mode=options.id_mode,
            skipped_sections=["records", "candidates", "relations", "tier_transitions"],
        )
