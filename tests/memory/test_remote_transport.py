from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.memory.runtime.remote_transport import (
    MemoryTransportError,
    RemoteMemoryStore,
    RemoteMemoryTransport,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    SearchQueryOptions,
)


def test_remote_transport_success_normalizes_data_payload() -> None:
    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=lambda envelope, timeout: {"ok": True, "data": {"record_id": "mem_1"}},
    )
    payload = transport.call(operation="put", payload={"record": {"id": "mem_1"}})
    assert payload["record_id"] == "mem_1"


def test_remote_transport_retries_timeout_then_succeeds() -> None:
    state = {"calls": 0}

    def _sender(envelope: dict[str, Any], timeout: float) -> dict[str, Any]:
        del envelope, timeout
        state["calls"] += 1
        if state["calls"] == 1:
            raise TimeoutError("simulated timeout")
        return {"ok": True, "data": {"record_id": "mem_2"}}

    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        max_retries=1,
        sender=_sender,
    )
    payload = transport.call(operation="put", payload={"record": {"id": "mem_2"}})
    assert payload["record_id"] == "mem_2"
    assert state["calls"] == 2


def test_remote_transport_raises_timeout_with_normalized_code() -> None:
    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        max_retries=0,
        sender=lambda envelope, timeout: (_ for _ in ()).throw(TimeoutError("timeout")),
    )
    with pytest.raises(MemoryTransportError) as exc_info:
        transport.call(operation="search", payload={"query": "x"})
    assert exc_info.value.code == "TIMEOUT"


def test_remote_transport_raises_backend_unavailable_with_normalized_code() -> None:
    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        max_retries=0,
        sender=lambda envelope, timeout: (_ for _ in ()).throw(OSError("down")),
    )
    with pytest.raises(MemoryTransportError) as exc_info:
        transport.call(operation="search", payload={"query": "x"})
    assert exc_info.value.code == "BACKEND_UNAVAILABLE"


def test_remote_transport_raises_invalid_argument_on_invalid_response_shape() -> None:
    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=lambda envelope, timeout: {"ok": True, "data": []},
    )
    with pytest.raises(MemoryTransportError) as exc_info:
        transport.call(operation="search", payload={"query": "x"})
    assert exc_info.value.code == "INVALID_ARGUMENT"


def test_remote_transport_propagates_normalized_remote_error_code() -> None:
    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=lambda envelope, timeout: {
            "ok": False,
            "error": {"code": "NOT_FOUND", "message": "missing"},
        },
    )
    with pytest.raises(MemoryTransportError) as exc_info:
        transport.call(operation="get", payload={"record_id": "missing"})
    assert exc_info.value.code == "NOT_FOUND"


def test_remote_memory_store_adapts_search_and_write_contracts() -> None:
    records = [
        {
            "id": "mem_1",
            "scope": "session:s1",
            "type": "fact",
            "title": "orion",
            "content": {"text": "orion remote"},
            "source": "validated",
            "confidence": 0.8,
            "created_at": "2026-03-11T00:00:00Z",
            "updated_at": "2026-03-11T00:00:00Z",
        }
    ]

    def _sender(envelope: dict[str, Any], timeout: float) -> dict[str, Any]:
        del timeout
        operation = envelope.get("operation")
        if operation == "put":
            return {"ok": True, "data": {"record_id": "mem_1"}}
        if operation == "search":
            return {"ok": True, "data": {"records": records}}
        return {"ok": True, "data": {}}

    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=_sender,
    )
    store = RemoteMemoryStore(transport)

    from openminion.modules.memory.models import MemoryRecord

    record_id = store.put(
        MemoryRecord(
            id="mem_1",
            scope="session:s1",
            type="fact",
            title="orion",
            content={"text": "orion remote"},
            source="validated",
            confidence=0.8,
            created_at="2026-03-11T00:00:00Z",
            updated_at="2026-03-11T00:00:00Z",
        )
    )
    assert record_id == "mem_1"

    hits = store.search(
        SearchQueryOptions(query="orion", scopes=["session:s1"], limit=5)
    )
    assert len(hits) == 1
    assert hits[0].id == "mem_1"


def test_remote_memory_store_candidate_payload_includes_scope_and_meta() -> None:
    seen_payloads: list[dict[str, Any]] = []

    def _sender(envelope: dict[str, Any], timeout: float) -> dict[str, Any]:
        del timeout
        seen_payloads.append(envelope)
        if envelope["operation"] == "candidate_list":
            return {
                "ok": True,
                "data": {
                    "candidates": [
                        {
                            "candidate_id": "cand_1",
                            "session_id": "s1",
                            "proposed_scope": "agent:test",
                            "type": "fact",
                            "content": "candidate text",
                            "status": "proposed",
                            "confidence": 0.4,
                            "meta": {"retrieval_hit_count": 2},
                            "created_at": "2026-03-27T00:00:00+00:00",
                            "updated_at": "2026-03-27T00:00:00+00:00",
                        }
                    ]
                },
            }
        if envelope["operation"] == "candidate_update":
            patch = envelope["payload"]["patch"]
            return {
                "ok": True,
                "data": {
                    "candidate": {
                        "candidate_id": envelope["payload"]["candidate_id"],
                        "session_id": "s1",
                        "proposed_scope": "agent:test",
                        "type": "fact",
                        "content": "candidate text",
                        "status": patch.get("status", "proposed"),
                        "confidence": patch.get("confidence", 0.6),
                        "meta": patch.get("meta", {"retrieval_hit_count": 3}),
                    }
                },
            }
        return {"ok": True, "data": {}}

    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=_sender,
    )
    store = RemoteMemoryStore(transport)

    candidates = store.candidate_list(
        CandidateListOptions(proposed_scope="agent:test", status="proposed")
    )
    assert len(candidates) == 1
    assert candidates[0].meta["retrieval_hit_count"] == 2

    updated = store.candidate_update(
        "cand_1",
        {
            "meta": {"retrieval_hit_count": 3},
            "confidence": 0.6,
        },
    )
    assert updated.meta["retrieval_hit_count"] == 3
    assert updated.confidence == 0.6

    candidate_list_payload = next(
        item["payload"]
        for item in seen_payloads
        if item["operation"] == "candidate_list"
    )
    assert candidate_list_payload["proposed_scope"] == "agent:test"


def test_remote_memory_store_apply_outcome_feedback_round_trips_count() -> None:
    seen_payloads: list[dict[str, Any]] = []

    def _sender(envelope: dict[str, Any], timeout: float) -> dict[str, Any]:
        del timeout
        seen_payloads.append(envelope)
        if envelope["operation"] == "apply_outcome_feedback":
            return {"ok": True, "data": {"updated_count": 2}}
        return {"ok": True, "data": {}}

    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=_sender,
    )
    store = RemoteMemoryStore(transport)

    updated = store.apply_outcome_feedback(
        ["mem_1", "mem_2"],
        outcome="timeout",
        command_id="cmd-1",
        observed_at="2026-03-28T00:00:00+00:00",
        feedback_delta=-0.05,
    )

    assert updated == 2
    payload = next(
        item["payload"]
        for item in seen_payloads
        if item["operation"] == "apply_outcome_feedback"
    )
    assert payload["record_ids"] == ["mem_1", "mem_2"]
    assert payload["feedback_delta"] == -0.05


def test_remote_memory_store_list_and_search_forward_include_invalidated() -> None:
    seen_payloads: list[dict[str, Any]] = []

    def _sender(envelope: dict[str, Any], timeout: float) -> dict[str, Any]:
        del timeout
        seen_payloads.append(envelope)
        return {"ok": True, "data": {"records": []}}

    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=_sender,
    )
    store = RemoteMemoryStore(transport)

    from openminion.modules.memory.storage.base import ListQueryOptions

    store.list(ListQueryOptions(scopes=["session:s1"], include_invalidated=True))
    store.search(
        SearchQueryOptions(
            query="alpha",
            scopes=["session:s1"],
            include_invalidated=True,
        )
    )

    list_payload = next(
        item["payload"] for item in seen_payloads if item["operation"] == "list"
    )
    search_payload = next(
        item["payload"] for item in seen_payloads if item["operation"] == "search"
    )
    assert list_payload["include_invalidated"] is True
    assert search_payload["include_invalidated"] is True


def test_remote_memory_store_invalidate_round_trip() -> None:
    seen_payloads: list[dict[str, Any]] = []

    def _sender(envelope: dict[str, Any], timeout: float) -> dict[str, Any]:
        del timeout
        seen_payloads.append(envelope)
        return {
            "ok": True,
            "data": {
                "record": {
                    "id": "mem_1",
                    "scope": "session:s1",
                    "type": "fact",
                    "content": {"text": "alpha"},
                    "created_at": "2026-05-21T00:00:00+00:00",
                    "updated_at": "2026-05-21T01:00:00+00:00",
                    "valid_to": "2026-05-21T01:00:00+00:00",
                }
            },
        }

    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=_sender,
    )
    store = RemoteMemoryStore(transport)

    record = store.invalidate(
        "mem_1",
        valid_to="2026-05-21T01:00:00+00:00",
        reason="corrected",
    )

    assert record.valid_to == "2026-05-21T01:00:00+00:00"
    payload = next(
        item["payload"] for item in seen_payloads if item["operation"] == "invalidate"
    )
    assert payload["record_id"] == "mem_1"
    assert payload["reason"] == "corrected"


def test_remote_memory_store_relation_round_trip() -> None:
    seen_payloads: list[dict[str, Any]] = []

    def _sender(envelope: dict[str, Any], timeout: float) -> dict[str, Any]:
        del timeout
        seen_payloads.append(envelope)
        if envelope["operation"] == "put_relation":
            return {"ok": True, "data": {"relation_id": "rel_1"}}
        if envelope["operation"] == "list_relations":
            return {
                "ok": True,
                "data": {
                    "relations": [
                        {
                            "relation_id": "rel_1",
                            "source_record_id": "mem_1",
                            "target_record_id": "mem_2",
                            "relation_type": "supports",
                            "created_at": "2026-04-30T00:00:00Z",
                            "meta": {"reason": "paired"},
                        }
                    ]
                },
            }
        if envelope["operation"] == "get_related_records":
            return {
                "ok": True,
                "data": {
                    "records": [
                        {
                            "id": "mem_2",
                            "scope": "session:s1",
                            "type": "fact",
                            "title": "beta",
                            "content": {"text": "beta"},
                            "source": "validated",
                            "confidence": 0.8,
                            "created_at": "2026-04-30T00:00:00Z",
                            "updated_at": "2026-04-30T00:00:00Z",
                        }
                    ]
                },
            }
        return {"ok": True, "data": {}}

    transport = RemoteMemoryTransport(
        endpoint="https://example.invalid/memory",
        sender=_sender,
    )
    store = RemoteMemoryStore(transport)

    from openminion.modules.memory.models import MemoryRelation

    relation_id = store.put_relation(
        MemoryRelation(
            relation_id="rel_1",
            source_record_id="mem_1",
            target_record_id="mem_2",
            relation_type="supports",
            created_at="2026-04-30T00:00:00Z",
            meta={"reason": "paired"},
        )
    )
    assert relation_id == "rel_1"

    relations = store.list_relations("mem_1", relation_types=["supports"])
    assert len(relations) == 1
    assert relations[0].target_record_id == "mem_2"

    related = store.get_related_records(
        "mem_1",
        scopes=["session:s1"],
        relation_types=["supports"],
    )
    assert [item.id for item in related] == ["mem_2"]
    assert seen_payloads[-1]["operation"] == "get_related_records"
