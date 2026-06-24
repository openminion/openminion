from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.runtime.gc import (
    apply_confidence_decay,
    evict_stale_insights,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.postgres.store import PostgresMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from tests.storage.postgres_test_utils import schema_url

pytestmark = pytest.mark.postgres


def _memory_config() -> object:
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        reflection=replace(
            cfg.reflection,
            reflection_enabled=True,
            reflection_interval_sessions=3,
            contradiction_similarity_threshold=0.8,
        ),
    )


def _make_adapter(
    tmp_path: Path,
) -> tuple[SQLiteMemoryStore, MemoryService, MemoryServiceGatewayAdapter]:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="truth-agent",
        memory_config=_memory_config(),
    )
    return store, service, adapter


def test_session_lifecycle_runs_capacity_and_purge(tmp_path: Path) -> None:
    store, service, adapter = _make_adapter(tmp_path)
    now = datetime.now(timezone.utc)

    for index in range(501):
        store.put(
            MemoryRecord(
                id=f"fact-{index}",
                scope="agent:truth-agent",
                type="fact",
                key=f"fact:{index}",
                title=f"fact {index}",
                content=f"active fact {index}",
                confidence=0.8,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )
    store.put(
        MemoryRecord(
            id="pin-1",
            scope="agent:truth-agent",
            type="pin",
            key="pin:1",
            title="Pinned runbook",
            content="Never drop the pinned runbook.",
            confidence=1.0,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
    )
    store.put(
        MemoryRecord(
            id="purge-me",
            scope="agent:truth-agent",
            type="fact",
            key="purge:me",
            title="Obsolete fact",
            content="obsolete fact",
            confidence=0.1,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
    )
    store.delete("purge-me")

    adapter._maybe_run_session_lifecycle(session_id="session-1")  # noqa: SLF001

    remaining = service.list(ListQueryOptions(scopes=["agent:truth-agent"], limit=600))
    assert len(remaining) <= 500
    assert any(record.id == "pin-1" for record in remaining)
    assert store.get("purge-me") is None


@pytest.mark.postgres
def test_session_lifecycle_runs_capacity_and_purge_on_postgres(
    tmp_path: Path,
) -> None:
    postgres_url = str(os.environ.get("OPENMINION_TEST_POSTGRES_URL", "")).strip()
    if not postgres_url:
        pytest.skip("OPENMINION_TEST_POSTGRES_URL is not set")
    schema_name = f"sfc_truth_{uuid.uuid4().hex}"
    admin_engine = sa.create_engine(postgres_url, future=True)
    with admin_engine.begin() as conn:
        conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    engine = sa.create_engine(schema_url(postgres_url, schema_name), future=True)
    try:
        store = PostgresMemoryStore(
            engine,
            database_path=Path.cwd() / ".openminion-memory-postgres-truth-test",
        )
        service = MemoryService(store=store)
        adapter = MemoryServiceGatewayAdapter(
            service,
            agent_id="truth-agent",
            memory_config=_memory_config(),
        )
        now = datetime.now(timezone.utc)

        for index in range(501):
            store.put(
                MemoryRecord(
                    id=f"fact-{index}",
                    scope="agent:truth-agent",
                    type="fact",
                    key=f"fact:{index}",
                    title=f"fact {index}",
                    content=f"active fact {index}",
                    confidence=0.8,
                    created_at=now.isoformat(),
                    updated_at=now.isoformat(),
                )
            )
        store.put(
            MemoryRecord(
                id="pin-1",
                scope="agent:truth-agent",
                type="pin",
                key="pin:1",
                title="Pinned runbook",
                content="Never drop the pinned runbook.",
                confidence=1.0,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )
        store.put(
            MemoryRecord(
                id="purge-me",
                scope="agent:truth-agent",
                type="fact",
                key="purge:me",
                title="Obsolete fact",
                content="obsolete fact",
                confidence=0.1,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )
        store.delete("purge-me")

        adapter._maybe_run_session_lifecycle(session_id="session-1")  # noqa: SLF001

        remaining = service.list(
            ListQueryOptions(scopes=["agent:truth-agent"], limit=600)
        )
        assert len(remaining) <= 500
        assert any(record.id == "pin-1" for record in remaining)
        assert store.get("purge-me") is None
    finally:
        engine.dispose()
        with admin_engine.begin() as conn:
            conn.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_disuse_decay_accelerates_records_without_recent_hits(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=45)).isoformat()
    recent_hit = (now - timedelta(days=5)).isoformat()

    store.put(
        MemoryRecord(
            id="recent-hit",
            scope="agent:truth-agent",
            type="fact",
            title="recent",
            content="recently used fact",
            confidence=0.8,
            last_hit_at=recent_hit,
            created_at=old,
            updated_at=old,
        )
    )
    store.put(
        MemoryRecord(
            id="stale-hit",
            scope="agent:truth-agent",
            type="fact",
            title="stale",
            content="stale fact",
            confidence=0.8,
            last_hit_at=None,
            created_at=old,
            updated_at=old,
        )
    )

    apply_confidence_decay(
        store,
        interval_days=7,
        decay_rate=0.05,
        min_confidence=0.0,
        disuse_threshold_days=30,
        disuse_decay_multiplier=2.0,
    )

    elapsed_intervals = max(1.0, 45 / 7)
    expected_confidence = 0.8 - (0.05 * elapsed_intervals)

    assert store.get("recent-hit").confidence == pytest.approx(expected_confidence)
    assert store.get("stale-hit").confidence == pytest.approx(expected_confidence)


def test_supersession_reason_and_keyed_upsert_history(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    now = "2026-03-01T00:00:00+00:00"

    store.put(
        MemoryRecord(
            id="old",
            scope="agent:truth-agent",
            type="fact",
            key="pref:theme",
            title="old",
            content="I prefer dark mode.",
            confidence=0.8,
            created_at=now,
            updated_at=now,
        )
    )
    store.put(
        MemoryRecord(
            id="new",
            scope="agent:truth-agent",
            type="fact",
            key="pref:theme:new",
            title="new",
            content="I prefer light mode.",
            confidence=0.8,
            created_at=now,
            updated_at=now,
        )
    )
    service.supersede_by_contradiction("old", "new", reason="direct_write")
    assert store.get("old").supersession_reason == "direct_write"

    service.upsert_record(
        scope="agent:truth-agent",
        record_type="fact",
        key="project:ci",
        record_patch={"title": "ci", "content": "CI uses deploy keys."},
    )
    updated = service.upsert_record(
        scope="agent:truth-agent",
        record_type="fact",
        key="project:ci",
        record_patch={"title": "ci", "content": "CI uses short-lived deploy keys."},
    )
    history = store.history("agent:truth-agent", "fact", "project:ci")
    assert updated.supersedes_id is not None
    assert any(record.supersession_reason == "keyed_upsert" for record in history)


def test_reflection_safety_rails_and_stale_insight_eviction(tmp_path: Path) -> None:
    store, service, adapter = _make_adapter(tmp_path)
    old = "2026-01-01T00:00:00+00:00"

    service.upsert_record(
        scope="agent:truth-agent",
        record_type="meta_insight",
        key="insight-correction:sleeps",
        record_patch={
            "title": "Recurring correction: Do not use ad hoc sleeps in tests.",
            "content": "The same correction has repeated across sessions: Do not use ad hoc sleeps in tests.",
            "tags": ["meta_insight", "recurring_correction"],
            "confidence": 0.8,
        },
    )
    for index in range(3):
        service.upsert_record(
            scope="agent:truth-agent",
            record_type="session_summary",
            key=f"session-summary:{index}",
            record_patch={
                "title": f"summary {index}",
                "content": {
                    "decisions": [],
                    "open_questions": [],
                    "corrections": ["Do not use ad hoc sleeps in tests."],
                    "topic_keywords": [
                        "pytest",
                        "deploy",
                        "infra",
                        "tools",
                        "lint",
                        "staging",
                    ],
                    "turn_count": 4,
                    "summary_text": "I prefer concise summaries. Do not use ad hoc sleeps in tests.",
                },
                "tags": ["session_summary"],
                "entities": ["pytest"],
                "source": "validated",
                "confidence": 0.8,
            },
        )

    written = adapter._maybe_run_reflection()  # noqa: SLF001
    insights = service.list(
        ListQueryOptions(
            scopes=["agent:truth-agent"],
            types=["meta_insight"],
            limit=20,
        )
    )
    assert written <= 5
    assert len(insights) <= 6  # existing duplicate + capped new writes

    stale = MemoryRecord(
        id="stale-insight",
        scope="agent:truth-agent",
        type="meta_insight",
        key="insight:stale",
        title="Stale insight",
        content="This insight is stale.",
        confidence=0.8,
        last_hit_at=None,
        created_at=old,
        updated_at=old,
    )
    store.put(stale)
    evicted = evict_stale_insights(store, staleness_days=60)
    assert evicted >= 1
    assert store.get("stale-insight").is_deleted is True
