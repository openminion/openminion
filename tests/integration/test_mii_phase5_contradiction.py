from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryRecord
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
            reflection_interval_sessions=3,
            contradiction_similarity_threshold=0.8,
        ),
    )


def test_phase5_semantic_supersession_and_reflection_integration(
    tmp_path: Path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase5-agent",
        memory_config=_memory_config(),
    )

    old_record = MemoryRecord(
        id="old-pref",
        scope="agent:phase5-agent",
        type="fact",
        key="pref:theme:dark",
        title="Dark mode preference",
        content="I prefer dark mode",
        created_at="2026-03-25T00:00:00+00:00",
        updated_at="2026-03-25T00:00:00+00:00",
        confidence=0.8,
    )
    new_record = MemoryRecord(
        id="new-pref",
        scope="agent:phase5-agent",
        type="fact",
        key="pref:theme:light",
        title="Light mode preference",
        content="I prefer light mode",
        created_at="2026-03-25T00:00:01+00:00",
        updated_at="2026-03-25T00:00:01+00:00",
        confidence=0.8,
    )
    store.put(old_record)
    store.put(new_record)
    service.supersede_by_contradiction("old-pref", "new-pref")

    for index in range(3):
        service.upsert_record(
            scope="agent:phase5-agent",
            record_type="session_summary",
            key=f"session_summary:s{index}",
            record_patch={
                "title": f"session {index}",
                "content": {
                    "decisions": ["decided to use pytest"],
                    "open_questions": [],
                    "corrections": ["use integration tests"],
                    "topic_keywords": ["pytest", "theme"],
                    "turn_count": 4,
                    "summary_text": "I prefer light mode. We use pytest. Use integration tests.",
                },
                "tags": ["session_summary"],
                "entities": ["pytest"],
                "source": "validated",
                "confidence": 0.8,
            },
        )

    written = adapter._maybe_run_reflection()  # noqa: SLF001
    assert written >= 1

    history = store.history("agent:phase5-agent", "fact", "pref:theme:dark")
    assert [record.id for record in history] == ["new-pref", "old-pref"]

    insights = service.list(
        ListQueryOptions(
            scopes=["agent:phase5-agent"],
            types=["meta_insight"],
            limit=20,
        )
    )
    assert insights
    capsule, _ = adapter.build_context_with_metadata(
        session_id="fresh-phase5",
        user_message="pytest",
    )
    assert "pytest" in capsule.lower()
