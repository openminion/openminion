from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory import cli as memory_cli
from openminion.modules.memory.config import RetentionConfig, from_base_config
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.diagnostics.operability import compute_stats
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from tests.storage.postgres_test_utils import schema_url

pytestmark = pytest.mark.postgres


def _memory_config(tmp_path: Path) -> object:
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )
    return replace(cfg, trace_file=tmp_path / "memory_trace.jsonl")


def _seed_operability_store(store: PostgresMemoryStore) -> None:
    now = "2026-03-28T00:00:00+00:00"
    store.put(
        MemoryRecord(
            id="fact-old",
            scope="agent:postgres-agent",
            type="fact",
            key="pref:indent",
            title="Prefer tabs",
            content="I prefer tabs.",
            confidence=0.55,
            created_at=now,
            updated_at=now,
        )
    )
    store.upsert(
        "agent:postgres-agent",
        "fact",
        "pref:indent",
        {"title": "Prefer spaces", "content": "I prefer spaces.", "confidence": 0.85},
    )
    store.put(
        MemoryRecord(
            id="corr-1",
            scope="agent:postgres-agent",
            type="correction",
            key="correction:ruff",
            title="Use ruff",
            content="Use ruff rather than flake8.",
            confidence=0.9,
            created_at=now,
            updated_at=now,
        )
    )
    deleted = MemoryRecord(
        id="deleted-1",
        scope="agent:postgres-agent",
        type="fact",
        key="deleted:1",
        title="deleted",
        content="deleted",
        confidence=0.2,
        created_at=now,
        updated_at=now,
    )
    store.put(deleted)
    store.delete("deleted-1")
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-1",
            session_id="sess-1",
            proposed_scope="agent:postgres-agent",
            type="fact",
            content="Candidate fact",
            status="proposed",
        )
    )
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-2",
            session_id="sess-1",
            proposed_scope="agent:postgres-agent",
            type="fact",
            content="Approved candidate",
            status="approved",
        )
    )


@pytest.fixture
def postgres_store():
    postgres_url = str(os.environ.get("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"sfc_memory_ops_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sa.create_engine(schema_url(postgres_url, schema_name), future=True)
    store = PostgresMemoryStore(
        engine,
        database_path=Path.cwd() / ".openminion-memory-postgres-operability-test",
    )
    try:
        yield store
    finally:
        try:
            store.close()
        finally:
            engine.dispose()
            with admin_engine.begin() as conn:
                conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
            admin_engine.dispose()


@pytest.mark.postgres
def test_compute_stats_dispatches_for_postgres_backend(
    postgres_store: PostgresMemoryStore,
) -> None:
    _seed_operability_store(postgres_store)

    stats = compute_stats(postgres_store, scope="agent:postgres-agent")

    assert stats["active_record_count"] >= 2
    assert stats["soft_deleted_count"] >= 1
    assert stats["supersession_chain_count"] >= 1
    assert stats["candidate_counts"]["approved"] == 1
    assert stats["candidate_counts"]["proposed"] == 1


@pytest.mark.postgres
def test_memctl_stats_inspect_and_gc_support_postgres_backend(
    postgres_store: PostgresMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_operability_store(postgres_store)
    trace_file = tmp_path / "memory_trace.jsonl"
    trace_file.write_text(
        json.dumps(
            {
                "event": "memory.context.built",
                "agent_id": "postgres-agent",
                "ts": "2026-03-28T00:01:00+00:00",
                "session_id": "sess-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    postgres_store.put(
        MemoryRecord(
            id="purge-me",
            scope="agent:postgres-agent",
            type="fact",
            key="purge:me",
            title="Obsolete fact",
            content="obsolete fact",
            confidence=0.1,
            created_at="2026-03-28T00:00:00+00:00",
            updated_at="2026-03-28T00:00:00+00:00",
        )
    )
    postgres_store.delete("purge-me")

    service = MemoryService(store=postgres_store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="postgres-agent",
        memory_config=_memory_config(tmp_path),
        trace_enabled=True,
    )
    adapter.build_context(
        session_id="sess-1",
        user_message="what are my preferences?",
    )

    retention = RetentionConfig(
        enable_soft_delete=True,
        gc_enabled=True,
        gc_batch_size=500,
        session_summary_max_chars=100,
        summary_compression_age_days=14,
        max_records_per_scope=500,
        confidence_decay_interval_days=7,
        confidence_decay_rate=0.05,
        min_confidence_eviction=0.3,
    )
    monkeypatch.setattr(
        memory_cli,
        "_get_service",
        lambda db=None: service,
    )
    monkeypatch.setattr(
        memory_cli,
        "load_config",
        lambda env=None: SimpleNamespace(
            store=SimpleNamespace(sqlite_path=None),
            retention=retention,
        ),
    )

    runner = CliRunner()
    app = memory_cli._build_app()

    stats = runner.invoke(app, ["stats", "--scope", "agent:postgres-agent", "--json"])
    assert stats.exit_code == 0, stats.output
    stats_payload = json.loads(stats.output)
    assert stats_payload["candidate_counts"]["approved"] == 1

    inspect = runner.invoke(
        app,
        [
            "inspect",
            "--scope",
            "agent:postgres-agent",
            "--trace-file",
            str(trace_file),
            "--json",
        ],
    )
    assert inspect.exit_code == 0, inspect.output
    inspect_payload = json.loads(inspect.output)
    assert inspect_payload["stats"]["candidate_counts"]["proposed"] == 1
    assert inspect_payload["recent_trace_events"][0]["event"] == "memory.context.built"

    gc = runner.invoke(app, ["gc"])
    assert gc.exit_code == 0, gc.output
    assert "GC: deleted_records=" in gc.output
