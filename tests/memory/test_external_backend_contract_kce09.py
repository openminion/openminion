from __future__ import annotations

import pytest

from openminion.modules.memory.backends.external import (
    ExternalBackendCapabilities,
    REFERENCE_SQLITE_ADAPTER_NAME,
    get_registered_external_backend,
    register_external_backend,
    register_reference_sqlite_backend,
    resolve_external_backend,
    validate_external_backend,
)
from openminion.modules.memory.backends.interfaces import KNOWLEDGE_BACKEND_VERSION
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.models import MemoryRecord


class _ExternalBackendStub:
    contract_version = KNOWLEDGE_BACKEND_VERSION

    def put_record(self, record):
        return "record-id"

    def upsert_record(self, scope, type, key, record_patch):
        return record_patch

    def get_record(self, record_id):
        return None

    def list_records(self, options):
        return []

    def search_records(self, options):
        return []

    def invalidate_record(self, record_id, *, valid_to, reason):
        return {"record_id": record_id}

    def supersede_record(self, old_record_id, new_record_id, reason=""):
        return {"old": old_record_id, "new": new_record_id}

    def put_relation(self, relation):
        return "rel-id"

    def list_relations(self, record_id, *, relation_types=None, limit=None):
        return []

    def get_related_records(
        self, record_id, scopes, *, relation_types=None, limit=None
    ):
        return []

    def put_candidate(self, candidate):
        return "candidate-id"

    def get_candidate(self, candidate_id):
        return None

    def list_candidates(self, options):
        return []

    def update_candidate(self, candidate_id, patch):
        return patch

    def promote_candidate(self, candidate_id, target_scope):
        return {"candidate": candidate_id, "scope": target_scope}

    def list_tier_transitions(self, *, record_id=None, scopes=None, limit=None):
        return []

    def put_tier_transition(self, transition):
        return "transition-id"

    def history(self, scope, type, key):
        return []

    def export_snapshot(self, options):
        return {"options": options}

    def import_snapshot(self, snapshot, options):
        return {"snapshot": snapshot, "options": options}


def test_external_backend_registry_round_trip() -> None:
    def _factory(**kwargs):
        assert kwargs["config"].external_adapter == "neo4j"
        return _ExternalBackendStub()

    register_external_backend(
        "neo4j",
        factory=_factory,
        capabilities=ExternalBackendCapabilities(supports_semantic_search=True),
    )
    registration = get_registered_external_backend("neo4j")
    assert registration.name == "neo4j"

    backend, report = resolve_external_backend(
        adapter="neo4j",
        config=type("Cfg", (), {"external_adapter": "neo4j"})(),
    )
    assert isinstance(backend, _ExternalBackendStub)
    assert report.ok is True
    assert report.missing_optional == ()


def test_external_backend_capability_validation_is_explicit() -> None:
    report = validate_external_backend(
        adapter="weak",
        backend=_ExternalBackendStub(),
        capabilities=ExternalBackendCapabilities(
            supports_relations=False,
            supports_candidate_workflow=False,
        ),
        strict=False,
    )
    assert report.ok is False
    assert "supports_relations" in report.missing_required
    assert "supports_candidate_workflow" in report.missing_required

    with pytest.raises(InvalidArgumentError, match="missing required capabilities"):
        validate_external_backend(
            adapter="weak",
            backend=_ExternalBackendStub(),
            capabilities=ExternalBackendCapabilities(
                supports_relations=False,
                supports_candidate_workflow=False,
            ),
            strict=True,
        )


def test_reference_sqlite_external_backend_uses_standalone_engine(tmp_path) -> None:
    register_reference_sqlite_backend()
    backend, report = resolve_external_backend(
        adapter=REFERENCE_SQLITE_ADAPTER_NAME,
        config=type(
            "Cfg",
            (),
            {
                "external_adapter": REFERENCE_SQLITE_ADAPTER_NAME,
                "options": {"db_path": str(tmp_path / "external.sqlite3")},
            },
        )(),
    )
    assert report.ok is True
    record_id = backend.put_record(
        MemoryRecord(
            id="ext-1",
            scope="agent:test",
            type="fact",
            content={"text": "external"},
            created_at="2026-05-22T00:00:00+00:00",
            updated_at="2026-05-22T00:00:00+00:00",
            source="validated",
            confidence=1.0,
            event_time="2026-05-22T00:00:00+00:00",
        )
    )
    assert record_id == "ext-1"
