from __future__ import annotations

import pytest

from openminion.modules.memory.contracts import (
    MEMORY_CONTRACT_VERSION,
    MemoryContractError,
    ensure_memory_contract_compatibility,
)


class _ReadClient:
    contract_version = MEMORY_CONTRACT_VERSION

    def search(self, query):  # noqa: ANN001
        del query
        return []

    def retrieve_by_entities(self, *, entities, scopes, types=None, limit=None):  # noqa: ANN001
        del entities, scopes, types, limit
        return []


class _WriteClient:
    contract_version = MEMORY_CONTRACT_VERSION

    def write_record(  # noqa: ANN001
        self, *, scope, record_type, title, content, tags=None, evidence_refs=None
    ):
        del scope, record_type, title, content, tags, evidence_refs
        return "mem_1"


class _CandidateClient:
    contract_version = MEMORY_CONTRACT_VERSION

    def stage_candidate(self, request):  # noqa: ANN001
        del request
        return "cand_1"

    def review_candidate(  # noqa: ANN001
        self, *, candidate_id, decision, reason_code="", metadata=None
    ):
        del candidate_id, decision, reason_code, metadata
        return {"decision": "approved"}

    def promote_candidate(self, *, candidate_id, target_scope):  # noqa: ANN001
        del candidate_id, target_scope
        return "mem_promoted"


class _ProcedureClient:
    contract_version = MEMORY_CONTRACT_VERSION

    def get_procedure(self, *, procedure_id):  # noqa: ANN001
        del procedure_id
        return None


class _IntrospectionClient:
    contract_version = MEMORY_CONTRACT_VERSION

    def get_runtime_snapshot(self, *, session_id, agent_id, max_highlights=5):  # noqa: ANN001
        del session_id, agent_id, max_highlights
        return {}


class _CapsuleClient:
    contract_version = MEMORY_CONTRACT_VERSION

    def build_capsule(self, *, session_id, agent_id, strategy):  # noqa: ANN001
        del session_id, agent_id, strategy
        return {}

    def refresh_capsule(self, *, session_id, agent_id, reason):  # noqa: ANN001
        del session_id, agent_id, reason
        return {}


class _LegacyServiceClient:
    contract_version = MEMORY_CONTRACT_VERSION

    def set_vector_adapter(self, vector_adapter):  # noqa: ANN001
        del vector_adapter

    def get(self, record_id):  # noqa: ANN001
        del record_id
        return None

    def list(self, options):  # noqa: ANN001
        del options
        return []

    def search(self, options):  # noqa: ANN001
        del options
        return []

    def search_semantic(self, query, scopes, *, types=None, limit=None):  # noqa: ANN001
        del query, scopes, types, limit
        return []

    def candidate_put(self, candidate):  # noqa: ANN001
        del candidate
        return "cand_legacy"

    def candidate_get(self, candidate_id):  # noqa: ANN001
        del candidate_id
        return None

    def candidate_list(self, options):  # noqa: ANN001
        del options
        return []

    def candidate_update(self, candidate_id, patch):  # noqa: ANN001
        del candidate_id, patch
        return {}

    def promote_candidate(self, candidate_id, target_scope):  # noqa: ANN001
        del candidate_id, target_scope
        return {}


@pytest.mark.parametrize(
    ("role", "component"),
    [
        ("read", _ReadClient()),
        ("write", _WriteClient()),
        ("candidate", _CandidateClient()),
        ("procedure", _ProcedureClient()),
        ("introspection", _IntrospectionClient()),
        ("capsule", _CapsuleClient()),
        ("service", _LegacyServiceClient()),
    ],
)
def test_contract_validator_accepts_valid_components(
    role: str, component: object
) -> None:
    ok, errors = ensure_memory_contract_compatibility(
        component,
        role=role,
        strict=False,
    )
    assert ok is True
    assert errors == []


def test_contract_validator_rejects_missing_member_for_role() -> None:
    class _BrokenRead:
        contract_version = MEMORY_CONTRACT_VERSION

        def search(self, query):  # noqa: ANN001
            del query
            return []

    ok, errors = ensure_memory_contract_compatibility(
        _BrokenRead(),
        role="read",
        strict=False,
    )
    assert ok is False
    assert any("missing member: retrieve_by_entities" in item for item in errors)


def test_contract_validator_rejects_version_drift() -> None:
    class _WrongVersion:
        contract_version = "v99"

        def write_record(  # noqa: ANN001
            self, *, scope, record_type, title, content, tags=None, evidence_refs=None
        ):
            del scope, record_type, title, content, tags, evidence_refs
            return "mem"

    ok, errors = ensure_memory_contract_compatibility(
        _WrongVersion(),
        role="write",
        strict=False,
    )
    assert ok is False
    assert any("version mismatch" in item for item in errors)


def test_contract_validator_strict_mode_raises() -> None:
    class _BrokenCandidate:
        contract_version = MEMORY_CONTRACT_VERSION

        def stage_candidate(self, request):  # noqa: ANN001
            del request
            return "cand"

    with pytest.raises(MemoryContractError):
        ensure_memory_contract_compatibility(
            _BrokenCandidate(),
            role="candidate",
            strict=True,
        )
