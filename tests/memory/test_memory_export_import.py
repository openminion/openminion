from __future__ import annotations

from pathlib import Path
import tarfile

import pytest

from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    MemoryRelation,
    MemoryTierTransition,
)
from openminion.modules.memory.errors import MemctlError
from openminion.modules.memory.portability.codec import (
    read_bundle_snapshot,
    write_bundle_snapshot,
)
from openminion.modules.memory.portability.models import (
    MemoryBundleExportOptions,
    MemoryBundleImportOptions,
    MemoryBundleSnapshot,
)
from openminion.modules.memory.runtime.promotion import PromotionPolicy
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore


def _service() -> MemoryService:
    return MemoryService(InMemoryMemoryStore(), PromotionPolicy())


def _sample_snapshot() -> MemoryBundleSnapshot:
    record = MemoryRecord(
        id="mem-1",
        scope="agent:source",
        type="fact",
        key="pref:lint",
        title="Lint tool",
        content="Use ruff",
        confidence=0.9,
        created_at="2026-05-11T00:00:00+00:00",
        updated_at="2026-05-11T00:00:00+00:00",
        event_time="2026-05-11T00:00:00+00:00",
        valid_to="2026-05-12T00:00:00+00:00",
    )
    candidate = MemoryCandidate(
        candidate_id="cand-1",
        session_id="sess-1",
        proposed_scope="agent:source",
        type="fact",
        key="cand:key",
        title="Candidate",
        content="candidate content",
        confidence=0.6,
    )
    relation = MemoryRelation(
        relation_id="rel-1",
        source_record_id="mem-1",
        target_record_id="mem-2",
        relation_type="related_to",
        created_at="2026-05-11T00:00:00+00:00",
    )
    transition = MemoryTierTransition(
        transition_id="mtt-1",
        record_id="mem-1",
        scope="agent:source",
        record_type="fact",
        from_tier="working",
        to_tier="archival",
        transition_reason="manual_override",
        transition_at="2026-05-11T00:00:00+00:00",
    )
    return MemoryBundleSnapshot(
        manifest={
            "bundle_id": "bundle-1",
            "created_at": "2026-05-11T00:00:00+00:00",
            "source_backend": "InMemoryMemoryStore",
            "source_instance": {"store_class": "InMemoryMemoryStore"},
            "scopes": ["agent:source"],
            "filters": {"types": [], "limit": None},
        },
        records=[
            record,
            MemoryRecord(
                id="mem-2",
                scope="agent:source",
                type="fact",
                key="pref:test",
                title="Test tool",
                content="Use pytest",
                confidence=0.8,
                created_at="2026-05-11T00:00:00+00:00",
                updated_at="2026-05-11T00:00:00+00:00",
            ),
        ],
        candidates=[candidate],
        relations=[relation],
        tier_transitions=[transition],
    )


def test_bundle_codec_round_trip(tmp_path: Path) -> None:
    snapshot = _sample_snapshot()
    bundle_path = tmp_path / "memory.tar.gz"

    written = write_bundle_snapshot(snapshot, bundle_path)
    loaded = read_bundle_snapshot(written)

    assert loaded.manifest["bundle_id"] == "bundle-1"
    assert len(loaded.records) == 2
    assert loaded.records[0].key == "pref:lint"
    assert loaded.records[0].event_time == "2026-05-11T00:00:00+00:00"
    assert loaded.records[0].valid_to == "2026-05-12T00:00:00+00:00"
    assert len(loaded.candidates) == 1
    assert len(loaded.relations) == 1
    assert len(loaded.tier_transitions) == 1


def test_bundle_codec_rejects_checksum_mismatch(tmp_path: Path) -> None:
    snapshot = _sample_snapshot()
    bundle_path = tmp_path / "memory.tar.gz"
    write_bundle_snapshot(snapshot, bundle_path)

    with tarfile.open(bundle_path, "r:gz") as archive:
        members = {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        }
    records_name = "memory-bundle/records.jsonl"
    members[records_name] = members[records_name].replace(b"Use ruff", b"Use flake8")
    with tarfile.open(bundle_path, "w:gz") as archive:
        import io

        for name, payload in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(MemctlError, match="checksum mismatch"):
        read_bundle_snapshot(bundle_path)


def test_service_export_bundle_snapshot_includes_requested_sections() -> None:
    service = _service()
    service._store.put(_sample_snapshot().records[0])  # noqa: SLF001
    service._store.put(_sample_snapshot().records[1])  # noqa: SLF001
    service.candidate_put(_sample_snapshot().candidates[0])
    service._store.put_relation(_sample_snapshot().relations[0])  # noqa: SLF001
    service.put_tier_transition(_sample_snapshot().tier_transitions[0])

    snapshot = service.export_bundle_snapshot(
        MemoryBundleExportOptions(
            scopes=["agent:source"],
            include_candidates=True,
            include_tier_history=True,
        )
    )

    assert snapshot.manifest["counts"]["records"] == 2
    assert snapshot.manifest["counts"]["candidates"] == 1
    assert snapshot.manifest["counts"]["relations"] == 1
    assert snapshot.manifest["counts"]["tier_transitions"] == 1


def test_direct_import_supersedes_existing_normalized_key_conflict() -> None:
    target = _service()
    target._store.put(  # noqa: SLF001
        MemoryRecord(
            id="existing-1",
            scope="agent:target",
            type="fact",
            key="pref:lint",
            title="Lint tool",
            content="Use flake8",
            confidence=0.4,
            created_at="2026-05-11T00:00:00+00:00",
            updated_at="2026-05-11T00:00:00+00:00",
        )
    )
    incoming = MemoryBundleSnapshot(
        manifest={
            "bundle_id": "bundle-2",
            "created_at": "2026-05-11T00:00:00+00:00",
            "source_backend": "InMemoryMemoryStore",
            "source_instance": {"store_class": "InMemoryMemoryStore"},
            "scopes": ["agent:source"],
            "filters": {"types": [], "limit": None},
        },
        records=[
            MemoryRecord(
                id="incoming-1",
                scope="agent:source",
                type="fact",
                key="pref:lint",
                title="Lint tool",
                content="Use ruff",
                confidence=0.9,
                created_at="2026-05-11T00:00:00+00:00",
                updated_at="2026-05-11T00:00:00+00:00",
            )
        ],
    )

    result = target.import_bundle_snapshot(
        incoming,
        MemoryBundleImportOptions(
            scope_rewrites={"agent:source": "agent:target"},
            trust_mode="direct",
            conflict_mode="supersede",
            id_mode="regenerate",
        ),
    )

    assert result.imported_records == 1
    records = target.list(ListQueryOptions(scopes=["agent:target"], limit=None))
    active = [
        record
        for record in records
        if not record.is_deleted and not record.superseded_by_id
    ]
    assert len(active) == 1
    assert active[0].content == "Use ruff"
    history = target._store.history("agent:target", "fact", "pref:lint")  # noqa: SLF001
    assert any(item.superseded_by_id for item in history)


def test_candidate_mode_import_stages_records_and_skips_durable_sections() -> None:
    service = _service()
    snapshot = _sample_snapshot()

    result = service.import_bundle_snapshot(
        snapshot,
        MemoryBundleImportOptions(
            trust_mode="candidate",
            conflict_mode="skip",
            id_mode="preserve",
        ),
    )

    assert result.staged_candidates == 2
    assert set(result.skipped_sections) == {
        "candidates",
        "relations",
        "tier_transitions",
    }
    candidates = service.candidate_list(
        CandidateListOptions(proposed_scope="agent:source", limit=None)
    )
    assert len(candidates) == 2
    records = service.list(ListQueryOptions(scopes=["agent:source"], limit=None))
    assert records == []
