from __future__ import annotations

from unittest.mock import Mock

import pytest

from openminion.modules.memory.backends import (
    DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER,
    KNOWLEDGE_BACKEND_VERSION,
    BuiltinKnowledgeBackend,
    KnowledgeBackendConfig,
    NoneKnowledgeBackend,
    ensure_backend_compatibility,
    get_registered_backend_factory,
    instantiate_backend,
    register_backend_factory,
    resolve_backend_config,
)
from openminion.modules.memory.errors import InvalidArgumentError
from openminion.modules.memory.models import MemoryCandidate, MemoryNamespace
from openminion.modules.memory.interfaces import ensure_memory_compatibility
from openminion.modules.memory.service import MemoryService
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    MemoryStore,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from sophiagraph.storage import SophiaGraphMemoryStore


class _BackendStub:
    contract_version = KNOWLEDGE_BACKEND_VERSION

    def put_record(self, record):
        return "record-id"

    def upsert_record(self, scope, type, key, record_patch):
        return {"scope": scope, "type": type, "key": key, "patch": record_patch}

    def get_record(self, record_id):
        return None

    def list_records(self, options):
        return []

    def search_records(self, options):
        return []

    def invalidate_record(self, record_id, *, valid_to, reason):
        return {"record_id": record_id, "valid_to": valid_to, "reason": reason}

    def supersede_record(self, old_record_id, new_record_id, reason=""):
        return {
            "old_record_id": old_record_id,
            "new_record_id": new_record_id,
            "reason": reason,
        }

    def put_relation(self, relation):
        return "relation-id"

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
        return {"candidate_id": candidate_id, "patch": patch}

    def promote_candidate(self, candidate_id, target_scope):
        return {"candidate_id": candidate_id, "target_scope": target_scope}

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


class TestKnowledgeBackendCompatibility:
    def test_valid_backend_passes_compatibility_check(self) -> None:
        success, errors = ensure_backend_compatibility(_BackendStub(), strict=False)
        assert success is True
        assert errors == []

    def test_memory_service_fails_backend_validation(self) -> None:
        service = MemoryService(store=Mock(spec=MemoryStore))
        success, errors = ensure_backend_compatibility(service, strict=False)
        assert success is False
        assert any("Missing required backend method" in error for error in errors)

    def test_backend_fails_memory_service_validation(self) -> None:
        success, errors = ensure_memory_compatibility(_BackendStub(), strict=False)
        assert success is False
        assert any("Missing required method" in error for error in errors)


class TestKnowledgeBackendConfig:
    def test_backend_config_defaults_to_sophiagraph(self) -> None:
        resolved = resolve_backend_config(None)
        assert resolved.provider == DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER
        assert resolved.external_adapter is None
        assert resolved.options == {}

    def test_backend_config_reads_external_provider(self) -> None:
        resolved = resolve_backend_config(
            {
                "backend": {
                    "provider": "external",
                    "external_adapter": "neo4j",
                    "options": {"mode": "read_only"},
                }
            }
        )
        assert resolved == KnowledgeBackendConfig(
            provider="external",
            external_adapter="neo4j",
            options={"mode": "read_only"},
        )

    def test_backend_config_rejects_unknown_provider(self) -> None:
        with pytest.raises(
            InvalidArgumentError, match="Unsupported memory.backend.provider"
        ):
            resolve_backend_config({"backend": {"provider": "bogus"}})

    def test_query_dto_reexports_now_come_from_sophiagraph_package(self) -> None:
        assert ListQueryOptions.__module__ == "sophiagraph.query.options"
        assert CandidateListOptions.__module__ == "sophiagraph.query.options"


class TestKnowledgeBackendFactory:
    def test_factory_registry_and_instantiation_validate_backend(self) -> None:
        provider = "unit_test_backend"

        def _build_backend(**kwargs):
            assert kwargs["config"].provider == provider
            return _BackendStub()

        register_backend_factory(provider, _build_backend)
        assert get_registered_backend_factory(provider) is _build_backend

        backend = instantiate_backend(config=KnowledgeBackendConfig(provider=provider))
        assert isinstance(backend, _BackendStub)


class TestBuiltinKnowledgeBackend:
    def test_memory_service_accepts_builtin_backend_without_behavior_regression(
        self,
    ) -> None:
        legacy_store = InMemoryMemoryStore()
        legacy_service = MemoryService(store=legacy_store)
        backend_service = MemoryService(
            backend=BuiltinKnowledgeBackend(InMemoryMemoryStore())
        )

        legacy_record = legacy_service.upsert_record(
            scope="agent:test",
            record_type="fact",
            key="shell",
            record_patch={"title": "Shell", "content": "zsh"},
        )
        backend_record = backend_service.upsert_record(
            scope="agent:test",
            record_type="fact",
            key="shell",
            record_patch={"title": "Shell", "content": "zsh"},
        )

        assert legacy_service.list(ListQueryOptions(scopes=["agent:test"])) == [
            legacy_record
        ]
        assert backend_service.list(ListQueryOptions(scopes=["agent:test"])) == [
            backend_record
        ]

        legacy_service.candidate_put(
            MemoryCandidate(
                candidate_id="cand-legacy",
                session_id="session-1",
                proposed_scope="agent:test",
                type="fact",
                title="shell preference",
                content="Uses zsh",
                confidence=0.7,
            )
        )
        backend_service.candidate_put(
            MemoryCandidate(
                candidate_id="cand-backend",
                session_id="session-1",
                proposed_scope="agent:test",
                type="fact",
                title="shell preference",
                content="Uses zsh",
                confidence=0.7,
            )
        )

        assert len(legacy_service.candidate_list(CandidateListOptions())) == 1
        assert len(backend_service.candidate_list(CandidateListOptions())) == 1

    def test_memory_service_submits_explicit_namespace_to_sophiagraph_backend(
        self,
    ) -> None:
        backend = SophiaGraphMemoryStore()
        service = MemoryService(backend=backend)

        record = service.upsert_record(
            scope="agent:codex",
            record_type="fact",
            key="shell",
            record_patch={"title": "Shell", "content": "Uses zsh"},
            agent_id="codex",
            session_id="session-1",
            conversation_id="conversation-1",
            project_id="project-sophiagraph",
            user_id="user-j",
            tenant_id="tenant-acme",
            graph_id="main",
        )

        assert record.namespace == MemoryNamespace(
            tenant_id="tenant-acme",
            user_id="user-j",
            agent_id="codex",
            session_id="session-1",
            conversation_id="conversation-1",
            project_id="project-sophiagraph",
            graph_id="main",
        )
        stored = backend.get_record(record.id)
        assert stored is not None
        assert stored.namespace == record.namespace

    def test_memory_service_rejects_store_and_backend_together(self) -> None:
        with pytest.raises(Exception, match="either store or backend"):
            MemoryService(
                store=InMemoryMemoryStore(),
                backend=BuiltinKnowledgeBackend(InMemoryMemoryStore()),
            )


class TestNoneKnowledgeBackend:
    def test_none_backend_returns_empty_reads_and_disabled_writes(self) -> None:
        backend = NoneKnowledgeBackend()
        service = MemoryService(backend=backend)

        assert service.list(ListQueryOptions(scopes=["agent:none"])) == []
        assert service.candidate_list(CandidateListOptions()) == []

        with pytest.raises(
            InvalidArgumentError, match="disables durable-memory writes"
        ):
            service.upsert_record(
                scope="agent:none",
                record_type="fact",
                key="shell",
                record_patch={"title": "Shell", "content": "zsh"},
            )

    def test_disabled_gateway_adapter_stays_stable_for_none_mode(self) -> None:
        adapter = DisabledMemoryGatewayAdapter(agent_id="none-agent")
        assert adapter.enabled is False
        assert adapter.build_context(session_id="s1", user_message="hi") == ""
