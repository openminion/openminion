from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from openminion.modules.memory.cli import _build_app
from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    MemoryRelation,
)
from openminion.modules.memory.runtime.promotion import PromotionPolicy
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _seed_source_store(db_path: Path) -> None:
    store = SQLiteMemoryStore(db_path)
    service = MemoryService(store, PromotionPolicy())
    now = "2026-05-11T00:00:00+00:00"
    store.put(
        MemoryRecord(
            id="mem-a",
            scope="agent:source",
            type="fact",
            key="pref:lint",
            title="Lint tool",
            content="Use ruff",
            confidence=0.9,
            created_at=now,
            updated_at=now,
        )
    )
    store.put(
        MemoryRecord(
            id="mem-b",
            scope="agent:source",
            type="fact",
            key="pref:test",
            title="Test tool",
            content="Use pytest",
            confidence=0.85,
            created_at=now,
            updated_at=now,
        )
    )
    store.put_relation(
        MemoryRelation(
            relation_id="rel-ab",
            source_record_id="mem-a",
            target_record_id="mem-b",
            relation_type="related_to",
            created_at=now,
        )
    )
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-a",
            session_id="sess-a",
            proposed_scope="agent:source",
            type="fact",
            key="cand:key",
            title="Candidate",
            content="candidate content",
            confidence=0.6,
        )
    )
    service.transition_tier(
        record_id="mem-a",
        to_tier="archival",
        transition_reason="manual_override",
        transition_at=now,
    )


def test_sqlite_bundle_round_trip_direct_full_fidelity(tmp_path: Path) -> None:
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    bundle_path = tmp_path / "bundle.tar.gz"
    _seed_source_store(source_db)

    runner = CliRunner()
    app = _build_app()

    export_result = runner.invoke(
        app,
        [
            "export",
            "--scope",
            "agent:source",
            "--bundle",
            "--include-candidates",
            "--include-tier-history",
            "--out",
            str(bundle_path),
            "--db",
            str(source_db),
        ],
    )
    assert export_result.exit_code == 0, export_result.output
    assert bundle_path.exists()

    import_result = runner.invoke(
        app,
        [
            "import",
            "--bundle",
            str(bundle_path),
            "--trust",
            "direct",
            "--db",
            str(target_db),
        ],
    )
    assert import_result.exit_code == 0, import_result.output
    assert "imported_records: 2" in import_result.output
    assert "imported_candidates: 1" in import_result.output
    assert "imported_relations: 1" in import_result.output
    assert "imported_tier_transitions: 1" in import_result.output

    target_service = MemoryService(SQLiteMemoryStore(target_db), PromotionPolicy())
    records = target_service.list(ListQueryOptions(scopes=["agent:source"], limit=None))
    assert {record.id for record in records} >= {"mem-a", "mem-b"}
    assert len(target_service.list_relations(record_id="mem-a", limit=None)) == 1
    assert (
        len(target_service.list_tier_transitions(scopes=["agent:source"], limit=None))
        == 1
    )
    assert (
        len(
            target_service.candidate_list(
                CandidateListOptions(proposed_scope="agent:source", limit=None)
            )
        )
        == 1
    )


def test_memctl_import_candidate_mode_stages_records_only(tmp_path: Path) -> None:
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    bundle_path = tmp_path / "bundle.tar.gz"
    _seed_source_store(source_db)

    runner = CliRunner()
    app = _build_app()
    export_result = runner.invoke(
        app,
        [
            "export",
            "--scope",
            "agent:source",
            "--bundle",
            "--include-candidates",
            "--include-tier-history",
            "--out",
            str(bundle_path),
            "--db",
            str(source_db),
        ],
    )
    assert export_result.exit_code == 0, export_result.output

    import_result = runner.invoke(
        app,
        [
            "import",
            "--bundle",
            str(bundle_path),
            "--trust",
            "candidate",
            "--json",
            "--db",
            str(target_db),
        ],
    )
    assert import_result.exit_code == 0, import_result.output
    payload = json.loads(import_result.output)
    assert payload["staged_candidates"] == 2
    assert set(payload["skipped_sections"]) == {
        "candidates",
        "relations",
        "tier_transitions",
    }

    target_service = MemoryService(SQLiteMemoryStore(target_db), PromotionPolicy())
    records = target_service.list(ListQueryOptions(scopes=["agent:source"], limit=None))
    assert records == []
    candidates = target_service.candidate_list(
        CandidateListOptions(proposed_scope="agent:source", limit=None)
    )
    assert len(candidates) == 2
