from __future__ import annotations

import datetime
from pathlib import Path

from openminion.modules.memory.config import RetentionConfig
from openminion.modules.memory.runtime.gc import run_gc
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _iso_days_ago(days: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).isoformat()


def test_phase3_gc_aging_with_real_sqlite(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")

    for index in range(50):
        age_days = 30 if index < 20 else 2
        confidence = 0.2 if index < 10 else 0.65
        store.put(
            MemoryRecord(
                id=f"rec-{index}",
                scope="agent:aging",
                type="fact",
                content=f"record {index}",
                created_at=_iso_days_ago(age_days),
                updated_at=_iso_days_ago(age_days),
                confidence=confidence,
                title=f"record {index}",
            )
        )
    store.put(
        MemoryRecord(
            id="pin-survivor",
            scope="agent:aging",
            type="pin",
            content="important pin",
            created_at=_iso_days_ago(60),
            updated_at=_iso_days_ago(60),
            confidence=0.1,
            title="important pin",
        )
    )

    result = run_gc(
        store,
        retention_config=RetentionConfig(
            enable_soft_delete=True,
            gc_enabled=True,
            gc_batch_size=1000,
            session_summary_max_chars=100,
            summary_compression_age_days=14,
            max_records_per_scope=20,
            confidence_decay_interval_days=7,
            confidence_decay_rate=0.05,
            min_confidence_eviction=0.3,
        ),
    )

    active = store.list(ListQueryOptions(scopes=["agent:aging"], limit=100))
    assert result.decayed_records >= 20
    assert result.capacity_evicted_records >= 1
    assert len(active) <= 20
    assert any(record.id == "pin-survivor" for record in active)
