from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions, SearchQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config():
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


def test_truth_maintenance_full_lifecycle(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="truth-agent",
        memory_config=_memory_config(),
    )
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=45)).isoformat()
    very_old = (now - timedelta(days=75)).isoformat()

    for index in range(20):
        store.put(
            MemoryRecord(
                id=f"seed-{index}",
                scope="agent:truth-agent",
                type="fact",
                key=f"seed:{index}",
                title=f"Seed {index}",
                content=f"stable fact {index}",
                confidence=0.8,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )

    store.put(
        MemoryRecord(
            id="stale-fact",
            scope="agent:truth-agent",
            type="fact",
            key="stale:fact",
            title="Legacy fact",
            content="The legacy rollout needs a static password.",
            confidence=0.35,
            last_hit_at=old,
            created_at=old,
            updated_at=old,
        )
    )
    store.put(
        MemoryRecord(
            id="pinned-fact",
            scope="agent:truth-agent",
            type="pin",
            key="pin:ops",
            title="Pinned ops rule",
            content="Never drop the pinned ops rule.",
            confidence=1.0,
            created_at=old,
            updated_at=old,
        )
    )

    adapter.build_context(session_id="lifecycle-1", user_message="legacy rollout")
    assert store.get("stale-fact") is None
    assert store.get("pinned-fact").is_deleted is False

    adapter.record_turn(
        session_id="truth-session",
        run_id="run-1",
        request_id="req-1",
        channel="eval",
        target="user",
        user_message="remember: tmux is required during pairing sessions.",
        assistant_message="Okay.",
    )
    adapter.record_turn(
        session_id="truth-session",
        run_id="run-2",
        request_id="req-2",
        channel="eval",
        target="user",
        user_message="remember: tmux is never required during pairing sessions.",
        assistant_message="Updated.",
    )

    results = service.search(
        SearchQueryOptions(
            query="pairing sessions",
            scopes=["agent:truth-agent"],
            limit=10,
        )
    )
    assert any("never required" in str(record.content).lower() for record in results)
    assert not any(
        str(record.id).startswith("seed-") and record.is_deleted for record in results
    )

    for index in range(3):
        service.upsert_record(
            scope="agent:truth-agent",
            record_type="session_summary",
            key=f"summary:{index}",
            record_patch={
                "title": f"summary {index}",
                "content": {
                    "decisions": [],
                    "open_questions": [],
                    "corrections": ["Do not use ad hoc sleeps in tests."],
                    "topic_keywords": ["pytest", "deploy", "staging"],
                    "turn_count": 4,
                    "summary_text": "Do not use ad hoc sleeps in tests. Prefer explicit waits.",
                },
                "tags": ["session_summary"],
                "entities": ["pytest"],
                "source": "validated",
                "confidence": 0.8,
            },
        )

    written = adapter._maybe_run_reflection()  # noqa: SLF001
    assert written <= 5
    insights = service.list(
        ListQueryOptions(
            scopes=["agent:truth-agent"],
            types=["meta_insight"],
            limit=20,
        )
    )
    assert insights

    oldest_insight = insights[-1]
    with store._connect() as conn:
        conn.execute(
            "UPDATE memory_records SET last_hit_at = ?, created_at = ?, updated_at = ? WHERE id = ?",
            (very_old, very_old, very_old, oldest_insight.id),
        )

        adapter.build_context(session_id="lifecycle-2", user_message="testing guidance")
        refreshed = store.get(oldest_insight.id)
        assert refreshed is None or refreshed.is_deleted is True

        final_records = service.search(
            SearchQueryOptions(
                query="tmux pairing sessions",
                scopes=["agent:truth-agent"],
                limit=10,
            )
        )
        final_texts = [str(record.content).lower() for record in final_records]
        final_text = "\n".join(final_texts)
        assert "never required during pairing sessions" in final_text
        assert any(
            "tmux is required during pairing sessions." in text for text in final_texts
        )
