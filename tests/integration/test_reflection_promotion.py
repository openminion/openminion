from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
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
            reflection_interval_sessions=5,
            contradiction_similarity_threshold=0.8,
            max_correction_promotions_per_run=1,
            max_preference_boosts_per_run=3,
        ),
    )


def test_reflection_promotion_full_cycle_with_caps(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="reflection-agent",
        memory_config=_memory_config(),
    )

    service.upsert_record(
        scope="agent:reflection-agent",
        record_type="user_preference",
        key="pref:dark-mode",
        record_patch={
            "title": "Preference: dark mode",
            "content": "I prefer dark mode.",
            "confidence": 0.4,
            "source": "validated",
        },
    )

    now = datetime(2026, 3, 27, tzinfo=timezone.utc)
    for index in range(15):
        service.upsert_record(
            scope="agent:reflection-agent",
            record_type="session_summary",
            key=f"summary:{index}",
            record_patch={
                "title": f"summary {index}",
                "content": {
                    "decisions": [],
                    "open_questions": [],
                    "corrections": [
                        "Do not use ad hoc sleeps in tests.",
                        "Use ruff rather than flake8.",
                    ],
                    "topic_keywords": [],
                    "preference_examples": [
                        {
                            "topic": "dark-mode",
                            "key": "pref:dark-mode",
                            "content": "I prefer dark mode.",
                            "title": "Preference: dark mode",
                        }
                    ],
                    "turn_count": 4,
                    "summary_text": (
                        "I prefer dark mode. "
                        "Do not use ad hoc sleeps in tests. "
                        "Use ruff rather than flake8."
                    ),
                },
                "tags": ["session_summary"],
                "entities": ["tests", "lint", "ui"],
                "source": "validated",
                "confidence": 0.8,
                "meta": {
                    "seeded_updated_at": (now - timedelta(days=index)).isoformat(),
                },
            },
        )

    written = adapter._maybe_run_reflection()  # noqa: SLF001

    preferences = service.list(
        ListQueryOptions(
            scopes=["agent:reflection-agent"],
            types=["user_preference"],
            limit=10,
        )
    )
    insights = service.list(
        ListQueryOptions(
            scopes=["agent:reflection-agent"],
            types=["meta_insight"],
            limit=20,
        )
    )

    assert isinstance(written, int)
    assert written >= 1

    # Preference-boost (still live via typed preference_examples).
    assert len(preferences) == 1
    assert preferences[0].key == "pref:dark-mode"
    assert preferences[0].confidence > 0.4

    # Stable-preference meta_insight (still live).
    stable_preference_insights = [
        record
        for record in insights
        if "stable_preference" in {str(tag) for tag in record.tags}
    ]
    assert stable_preference_insights
    assert "boosted_at" in stable_preference_insights[0].meta
    assert stable_preference_insights[0].meta["boosted_record_key"] == "pref:dark-mode"

    # Recurring-correction meta_insight (still live).
    recurring_correction_insights = [
        record
        for record in insights
        if "recurring_correction" in {str(tag) for tag in record.tags}
    ]
    assert len(recurring_correction_insights) >= 1
