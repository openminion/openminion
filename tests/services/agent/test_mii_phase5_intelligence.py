from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(
    *, reflection_enabled: bool = True, reflection_interval_sessions: int = 3
):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        reflection=replace(
            cfg.reflection,
            reflection_enabled=reflection_enabled,
            reflection_interval_sessions=reflection_interval_sessions,
            contradiction_similarity_threshold=0.8,
        ),
    )


def _make_adapter():
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase5-agent",
        memory_config=_memory_config(),
    )
    return store, service, adapter


def test_phase5_meta_insight_type_and_reflection_defaults_validate() -> None:
    cfg = _memory_config()
    record = MemoryRecord(
        id="meta-1",
        scope="agent:phase5-agent",
        type="meta_insight",
        content="Recurring topic insight",
        created_at="2026-03-25T00:00:00+00:00",
        updated_at="2026-03-25T00:00:00+00:00",
        confidence=0.8,
    )
    assert record.type == "meta_insight"
    assert cfg.reflection.reflection_enabled is True
    assert cfg.reflection.reflection_interval_sessions == 3
    assert cfg.reflection.contradiction_similarity_threshold == 0.8


def test_supersede_by_contradiction_reuses_chain_and_history(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    old_record = MemoryRecord(
        id="old-pref",
        scope="agent:phase5-agent",
        type="fact",
        key="pref:theme:dark",
        title="Dark mode preference",
        content="I prefer dark mode",
        created_at="2026-03-25T00:00:00+00:00",
        updated_at="2026-03-25T00:00:00+00:00",
        confidence=0.9,
    )
    new_record = MemoryRecord(
        id="new-pref",
        scope="agent:phase5-agent",
        type="fact",
        key="pref:theme:light",
        title="Light mode preference",
        content="I prefer light mode",
        created_at="2026-03-25T00:00:00+00:00",
        updated_at="2026-03-25T00:00:00+00:00",
        confidence=0.9,
    )
    store.put(old_record)
    store.put(new_record)

    updated = service.supersede_by_contradiction("old-pref", "new-pref")

    assert updated.supersedes_id == "old-pref"
    assert updated.key == "pref:theme:dark"
    assert store.get("old-pref").superseded_by_id == "new-pref"
    assert store.get("old-pref").is_deleted is True
    history = store.history("agent:phase5-agent", "fact", "pref:theme:dark")
    assert {record.id for record in history} == {"new-pref", "old-pref"}


def test_run_reflection_and_interval_wiring_write_meta_insights() -> None:
    _store, service, adapter = _make_adapter()
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
                    "topic_keywords": ["pytest", "integration"],
                    "turn_count": 4,
                    "summary_text": "I prefer dark mode. We use pytest. Use integration tests.",
                },
                "tags": ["session_summary"],
                "entities": ["pytest"],
                "source": "validated",
                "confidence": 0.8,
            },
        )

    insights = adapter._run_reflection(  # noqa: SLF001
        service.list(
            ListQueryOptions(
                scopes=["agent:phase5-agent"],
                types=["session_summary"],
                limit=10,
            )
        )
    )
    assert any("Recurring topic: pytest" == item["title"] for item in insights)
    assert any(item["title"].startswith("Recurring correction:") for item in insights)
    assert not any(item["title"].startswith("Stable preference:") for item in insights)

    written = adapter._maybe_run_reflection()  # noqa: SLF001
    assert written >= 1
    meta_insights = service.list(
        ListQueryOptions(
            scopes=["agent:phase5-agent"],
            types=["meta_insight"],
            limit=20,
        )
    )
    assert meta_insights
