from __future__ import annotations

from typing import Any, Iterator

import pytest

from openminion.modules.memory.contracts.provenance import (
    MemoryProvenanceEntry,
    TurnProvenanceTrace,
)
from openminion.modules.memory.errors import MemctlError
from openminion.modules.memory.models import MemoryRecord


# --- Provenance contract tests (MPF-01) ----------------------------------


class TestMemoryProvenanceEntry:
    def test_round_trip(self):
        entry = MemoryProvenanceEntry(
            memory_id="mem-1",
            source="tool_output",
            written_at="2026-05-18T00:00:00Z",
            retrieval_score=0.87,
            score_breakdown={"relevance": 0.5, "recency": 0.2},
            citation_context="answer_grounding",
        )
        payload = entry.to_dict()
        revived = MemoryProvenanceEntry.from_dict(payload)
        assert revived == entry

    def test_rejects_empty_memory_id(self):
        with pytest.raises(MemctlError, match="memory_id"):
            MemoryProvenanceEntry(
                memory_id="",
                source="user_said",
                written_at="2026-05-18T00:00:00Z",
                retrieval_score=0.5,
            )

    def test_rejects_oversize_citation_context(self):
        with pytest.raises(MemctlError, match="citation_context"):
            MemoryProvenanceEntry(
                memory_id="m",
                source="user_said",
                written_at="2026-05-18T00:00:00Z",
                retrieval_score=0.5,
                citation_context="x" * 201,
            )


class TestTurnProvenanceTrace:
    def test_round_trip(self):
        trace = TurnProvenanceTrace(
            session_id="sess-1",
            turn_id="turn-2",
            recorded_at="2026-05-18T00:00:00Z",
            entries=(
                MemoryProvenanceEntry(
                    memory_id="m1",
                    source="tool_output",
                    written_at="2026-05-17T12:00:00Z",
                    retrieval_score=0.9,
                ),
                MemoryProvenanceEntry(
                    memory_id="m2",
                    source="user_said",
                    written_at="2026-05-17T13:00:00Z",
                    retrieval_score=0.7,
                ),
            ),
            retrieval_cutoff=0.5,
            query="who is the user",
        )
        payload = trace.to_dict()
        revived = TurnProvenanceTrace.from_dict(payload)
        assert revived == trace
        assert revived.memory_ids() == ["m1", "m2"]

    def test_rejects_non_entry_in_entries(self):
        with pytest.raises(TypeError):
            TurnProvenanceTrace(
                session_id="s",
                turn_id="t",
                recorded_at="now",
                entries=({"memory_id": "m"},),  # type: ignore[arg-type]
            )


# --- delete_record + forget_by_source contract tests (MPF-05) ------------


class _RecordingStore:
    def __init__(self, records: list[MemoryRecord]) -> None:
        self._records: dict[str, MemoryRecord] = {r.id: r for r in records}
        self.delete_calls: list[dict[str, Any]] = []

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    def delete(
        self,
        record_id: str,
        *,
        reason: str | None = None,
        deleted_at: str | None = None,
    ) -> None:
        # Store supports the MPF-05 audit kwargs.
        self.delete_calls.append(
            {"id": record_id, "reason": reason, "deleted_at": deleted_at}
        )
        rec = self._records.get(record_id)
        if rec is not None:
            self._records[record_id] = MemoryRecord(
                **{
                    **rec.__dict__,
                    "is_deleted": True,
                    "deleted_at": deleted_at,
                    "deleted_reason": reason,
                }
            )

    def iter_all_records(self) -> Iterator[MemoryRecord]:
        return iter(self._records.values())


class _LegacyStore:
    def __init__(self, records: list[MemoryRecord]) -> None:
        self._records: dict[str, MemoryRecord] = {r.id: r for r in records}
        self.legacy_deletes: list[str] = []

    def get(self, record_id: str) -> MemoryRecord | None:
        return self._records.get(record_id)

    def delete(self, record_id: str) -> None:
        self.legacy_deletes.append(record_id)


def _make_record(record_id: str, *, source: str = "tool_output") -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="agent:test",
        type="fact",
        content={"text": f"content for {record_id}"},
        created_at="2026-05-18T00:00:00Z",
        updated_at="2026-05-18T00:00:00Z",
        source=source,
    )


@pytest.fixture
def memory_service_with_recording_store():

    from openminion.modules.memory.service import MemoryService

    # These tests only touch ``self._store.get`` and ``self._store.delete``.
    svc = MemoryService.__new__(MemoryService)
    svc._store = _RecordingStore(  # type: ignore[attr-defined]
        [
            _make_record("m1", source="tool_output"),
            _make_record("m2", source="tool_output"),
            _make_record("m3", source="agent_inferred"),
        ]
    )
    return svc


@pytest.fixture
def memory_service_with_legacy_store():
    from openminion.modules.memory.service import MemoryService

    svc = MemoryService.__new__(MemoryService)
    svc._store = _LegacyStore(  # type: ignore[attr-defined]
        [_make_record("legacy-1", source="user_said")]
    )
    return svc


class TestDeleteRecordWithReason:
    def test_legacy_path_no_reason_still_works(
        self, memory_service_with_recording_store
    ):
        svc = memory_service_with_recording_store
        assert svc.delete_record("m1") is True
        call = svc._store.delete_calls[0]  # type: ignore[attr-defined]
        # No reason → no audit fields populated.
        assert call == {"id": "m1", "reason": None, "deleted_at": None}

    def test_returns_false_for_missing_record(
        self, memory_service_with_recording_store
    ):
        svc = memory_service_with_recording_store
        assert svc.delete_record("no-such-id", reason="audit") is False

    def test_with_reason_populates_audit_fields(
        self, memory_service_with_recording_store
    ):
        svc = memory_service_with_recording_store
        assert svc.delete_record("m1", reason="operator request") is True
        call = svc._store.delete_calls[0]  # type: ignore[attr-defined]
        assert call["id"] == "m1"
        assert call["reason"] == "operator request"
        assert call["deleted_at"] is not None
        # deleted_at must be ISO-shaped (sanity check).
        assert "T" in call["deleted_at"]

    def test_legacy_store_falls_back_cleanly(self, memory_service_with_legacy_store):
        svc = memory_service_with_legacy_store
        # without the audit kwargs — and the service returns True.
        assert svc.delete_record("legacy-1", reason="audit") is True
        assert svc._store.legacy_deletes == ["legacy-1"]  # type: ignore[attr-defined]


class TestForgetBySource:
    def test_refuses_empty_reason(self, memory_service_with_recording_store):
        svc = memory_service_with_recording_store
        with pytest.raises(MemctlError, match="reason"):
            svc.forget_by_source("tool_output", reason="", dry_run=False)

    def test_refuses_empty_source(self, memory_service_with_recording_store):
        svc = memory_service_with_recording_store
        with pytest.raises(MemctlError, match="source"):
            svc.forget_by_source("", reason="audit", dry_run=False)

    def test_dry_run_returns_matches_without_mutating(
        self, memory_service_with_recording_store
    ):
        svc = memory_service_with_recording_store
        matched = svc.forget_by_source(
            "tool_output", reason="audit cleanup", dry_run=True
        )
        assert set(matched) == {"m1", "m2"}
        # No delete calls because dry_run=True.
        assert svc._store.delete_calls == []  # type: ignore[attr-defined]

    def test_apply_mutates_matched_records(self, memory_service_with_recording_store):
        svc = memory_service_with_recording_store
        matched = svc.forget_by_source(
            "tool_output", reason="audit cleanup", dry_run=False
        )
        assert set(matched) == {"m1", "m2"}
        deleted_ids = {call["id"] for call in svc._store.delete_calls}  # type: ignore[attr-defined]
        assert deleted_ids == {"m1", "m2"}
        # The agent_inferred record is untouched.
        store = svc._store  # type: ignore[attr-defined]
        agent_record = store.get("m3")
        assert agent_record is not None
        assert agent_record.is_deleted is False

    def test_apply_idempotent_on_already_deleted(
        self, memory_service_with_recording_store
    ):
        svc = memory_service_with_recording_store
        # First pass deletes.
        first = svc.forget_by_source("tool_output", reason="first", dry_run=False)
        # Second pass — all records are now is_deleted=True and skipped.
        second = svc.forget_by_source("tool_output", reason="second", dry_run=False)
        assert set(first) == {"m1", "m2"}
        assert second == []
