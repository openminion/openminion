from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import RetentionConfig, from_base_config
from openminion.modules.memory.runtime.gc import (
    apply_confidence_decay,
    enforce_scope_capacity,
    run_gc,
)
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.modules.memory.service import MemoryService
from tests._csc_fixtures import _csc_install_default_agent


def _iso_days_ago(days: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).isoformat()


def _record(
    record_id: str,
    *,
    scope: str = "agent:lifecycle",
    record_type: str = "fact",
    confidence: float = 0.5,
    age_days: int = 10,
    content: str | dict = "content",
    key: str | None = None,
) -> MemoryRecord:
    when = _iso_days_ago(age_days)
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type=record_type,  # type: ignore[arg-type]
        content=content,
        created_at=when,
        updated_at=when,
        confidence=confidence,
        key=key,
        title=record_id,
    )


def test_phase3_retention_config_defaults() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    cfg = from_base_config(
        base_config=config,
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    assert cfg.retention.max_records_per_scope == 500
    assert cfg.retention.summary_compression_age_days == 14
    assert cfg.retention.confidence_decay_interval_days == 7
    assert cfg.retention.confidence_decay_rate == 0.05
    assert cfg.retention.min_confidence_eviction == 0.3
    assert cfg.retention.summary_compression_max_chars == 100
    assert cfg.retention.summary_delete_age_days == 90


def test_apply_confidence_decay_honors_threshold_and_exemptions(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.put(_record("decay-me", confidence=0.7, age_days=30))
    store.put(_record("evict-me", confidence=0.31, age_days=30))
    store.put(_record("pin-me", record_type="pin", confidence=0.2, age_days=30))
    store.put(_record("session-me", scope="session:s1", confidence=0.2, age_days=30))
    store.put(_record("recent-me", confidence=0.9, age_days=1))

    decayed, evicted = apply_confidence_decay(
        store,
        interval_days=7,
        decay_rate=0.05,
        min_confidence=0.3,
    )

    assert decayed == 2
    assert evicted == 1
    decayed_survivor = store.get("decay-me")
    assert decayed_survivor.confidence == pytest.approx(0.7 - (0.05 * 30 / 7), rel=1e-3)
    assert decayed_survivor.is_deleted is False
    assert store.get("evict-me").is_deleted is True
    assert store.get("pin-me").is_deleted is False
    assert store.get("session-me").is_deleted is False
    assert store.get("recent-me").confidence == 0.9


def test_enforce_scope_capacity_evicts_lowest_confidence_non_pins(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    for index in range(8):
        store.put(
            _record(
                f"fact-{index}",
                confidence=0.1 + (index * 0.1),
                age_days=10,
            )
        )
    store.put(_record("pin-survivor", record_type="pin", confidence=0.05, age_days=10))

    evicted = enforce_scope_capacity(store, max_records=5)

    active = store.list(ListQueryOptions(scopes=["agent:lifecycle"], limit=20))
    assert evicted == {"agent:lifecycle": 4}
    assert len(active) == 5
    assert any(record.id == "pin-survivor" for record in active)


def test_run_gc_applies_decay_compression_capacity_and_purge(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    MemoryServiceGatewayAdapter(service, agent_id="lifecycle")

    store.put(_record("low-old", confidence=0.31, age_days=30))
    for index in range(6):
        store.put(_record(f"cap-{index}", confidence=0.4 + (index * 0.05), age_days=10))
    store.put(
        _record(
            "summary-old",
            record_type="session_summary",
            age_days=45,
            confidence=0.9,
            key="session_summary:old",
            content={
                "decisions": ["decided to use pytest"],
                "open_questions": ["what remains?"],
                "corrections": ["actually, wrong fixture"],
                "topic_keywords": ["pytest", "fixture"],
                "turn_count": 4,
                "summary_text": "Decided to use pytest. Actually, wrong fixture scope for db setup.",
            },
        )
    )
    store.put(
        _record(
            "summary-ancient",
            record_type="session_summary",
            age_days=100,
            confidence=0.9,
            key="session_summary:ancient",
            content={
                "decisions": ["old decision"],
                "open_questions": [],
                "corrections": [],
                "topic_keywords": ["legacy"],
                "turn_count": 2,
                "summary_text": "Old session to remove.",
            },
        )
    )

    result = run_gc(
        store,
        retention_config=RetentionConfig(
            enable_soft_delete=True,
            gc_enabled=True,
            gc_batch_size=1000,
            session_summary_max_chars=80,
            session_summary_checkpoint_message_interval=2,
            summary_compression_age_days=14,
            max_records_per_scope=5,
            confidence_decay_interval_days=7,
            confidence_decay_rate=0.05,
            min_confidence_eviction=0.3,
        ),
    )

    compressed = store.get("summary-old")
    assert result.decayed_records >= 1
    assert result.capacity_evicted_records >= 1
    assert result.compressed_summaries == 1
    assert compressed is not None
    assert isinstance(compressed.content, dict)
    assert compressed.content["decisions"] == ["decided to use pytest"]
    assert compressed.content["corrections"] == ["actually, wrong fixture"]
    assert compressed.content["open_questions"] == ["what remains?"]
    assert store.get("summary-ancient") is None
    assert store.get("low-old") is None


def test_adapter_compress_old_summaries_delegates_to_gc(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(service, agent_id="lifecycle")
    store.put(
        _record(
            "summary-gateway",
            record_type="session_summary",
            age_days=45,
            key="session_summary:gateway",
            content={
                "decisions": ["decided to use pytest"],
                "open_questions": ["what remains?"],
                "corrections": ["actually, wrong fixture"],
                "topic_keywords": ["pytest", "fixture"],
                "turn_count": 4,
                "summary_text": "Decided to use pytest. Actually, wrong fixture scope for db setup.",
            },
        )
    )

    compressed, deleted = adapter.compress_old_summaries(
        max_age_days=30,
        max_summary_chars=50,
    )

    record = store.get("summary-gateway")
    assert compressed == 1
    assert deleted == 0
    assert isinstance(record.content, dict)
    assert record.content["decisions"] == ["decided to use pytest"]


def test_session_lifecycle_partial_retention_object_fails_loudly_post_mref(
    tmp_path: Path,
    caplog,
) -> None:

    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="lifecycle",
        memory_config=SimpleNamespace(retention=SimpleNamespace(gc_enabled=True)),
    )

    with (
        mock.patch(
            "openminion.modules.memory.runtime.session_lifecycle.apply_confidence_decay",
            return_value=(0, 0),
        ) as decay,
        mock.patch(
            "openminion.modules.memory.runtime.session_lifecycle.compress_old_summaries_gc",
            return_value=(0, 0),
        ) as compress,
        mock.patch(
            "openminion.modules.memory.runtime.session_lifecycle.enforce_scope_capacity",
            return_value={},
        ) as capacity,
        mock.patch(
            "openminion.modules.memory.runtime.session_lifecycle.evict_stale_insights",
            return_value=0,
        ) as stale,
        mock.patch(
            "openminion.modules.memory.runtime.session_lifecycle.purge_soft_deleted",
            return_value=SimpleNamespace(
                deleted_records=0,
                deleted_candidates=0,
                cleaned_fts_rows=0,
                cleaned_entity_rows=0,
            ),
        ) as purge,
    ):
        with caplog.at_level("WARNING"):
            adapter._maybe_run_session_lifecycle(session_id="synthetic")  # noqa: SLF001

    # Step 1 (decay) still uses `getattr(retention, ..., default)` reads
    # for non-MREF fields, so it runs successfully.
    decay.assert_called_once()
    compress.assert_not_called()
    capacity.assert_not_called()
    stale.assert_not_called()
    purge.assert_not_called()
    # The lifecycle logs the failure with the typed-field name so the
    # cause is greppable from production telemetry.
    assert any(
        "memory.session_lifecycle failed" in record.message
        and "summary_compression_age_days" in record.message
        for record in caplog.records
    )
