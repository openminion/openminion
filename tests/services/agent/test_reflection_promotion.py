from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import RankingConfig, from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.runtime.scorer import score_record
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(
    *,
    reflection_interval_sessions: int = 5,
    promotion_enabled: bool = True,
    max_correction_promotions_per_run: int = 2,
    max_preference_boosts_per_run: int = 3,
    ranking: RankingConfig | None = None,
):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        ranking=ranking or cfg.ranking,
        reflection=replace(
            cfg.reflection,
            reflection_enabled=True,
            reflection_interval_sessions=reflection_interval_sessions,
            contradiction_similarity_threshold=0.8,
            promotion_enabled=promotion_enabled,
            max_correction_promotions_per_run=max_correction_promotions_per_run,
            max_preference_boosts_per_run=max_preference_boosts_per_run,
        ),
    )


def _make_adapter(**config_kwargs):
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="reflection-agent",
        memory_config=_memory_config(**config_kwargs),
    )
    return store, service, adapter


def _put_summary(
    store: InMemoryMemoryStore,
    *,
    record_id: str,
    updated_at: str,
    summary_text: str,
    corrections: list[str] | None = None,
    topic_keywords: list[str] | None = None,
) -> None:
    store.put(
        MemoryRecord(
            id=record_id,
            scope="agent:reflection-agent",
            type="session_summary",
            key=f"session_summary:{record_id}",
            title=record_id,
            content={
                "decisions": [],
                "open_questions": [],
                "corrections": list(corrections or []),
                "topic_keywords": list(topic_keywords or []),
                "turn_count": 4,
                "summary_text": summary_text,
            },
            tags=["session_summary"],
            entities=sorted({*(topic_keywords or [])}),
            source="validated",
            confidence=0.8,
            created_at=updated_at,
            updated_at=updated_at,
        )
    )


def _put_insight(
    store: InMemoryMemoryStore,
    *,
    record_id: str,
    key: str,
    title: str,
    content: str,
    tags: list[str],
    meta: dict[str, object] | None = None,
    confidence: float = 0.85,
    updated_at: str = "2026-03-27T00:00:00+00:00",
) -> None:
    store.put(
        MemoryRecord(
            id=record_id,
            scope="agent:reflection-agent",
            type="meta_insight",
            key=key,
            title=title,
            content=content,
            tags=tags,
            entities=[],
            source="agent_inferred",
            confidence=confidence,
            meta=dict(meta or {}),
            created_at=updated_at,
            updated_at=updated_at,
        )
    )


def test_meta_insight_type_bonus_scores_above_fact() -> None:
    ranking = RankingConfig(
        type_boost_correction=1.02,
        type_boost_user_preference=1.01,
        type_boost_pin=1.01,
        type_boost_project_convention=1.01,
        type_boost_meta_insight=1.05,
    )
    fact = MemoryRecord(
        id="fact",
        scope="agent:reflection-agent",
        type="fact",
        title="fact",
        content="plain fact",
        created_at="2026-03-27T00:00:00+00:00",
        updated_at="2026-03-27T00:00:00+00:00",
        confidence=0.8,
        meta={"bm25_score": 0.8},
    )
    insight = MemoryRecord(
        id="insight",
        scope="agent:reflection-agent",
        type="meta_insight",
        title="insight",
        content="plain fact",
        created_at="2026-03-27T00:00:00+00:00",
        updated_at="2026-03-27T00:00:00+00:00",
        confidence=0.8,
        meta={"bm25_score": 0.8},
    )

    fact_scored = score_record(fact, ranking_config=ranking)
    insight_scored = score_record(insight, ranking_config=ranking)

    assert insight_scored.meta["score_breakdown"]["type_bonus"] > 0.0
    assert float(insight_scored.meta["unified_score"]) > float(
        fact_scored.meta["unified_score"]
    )


def test_boost_stable_preferences_noops_without_structured_preference_signals() -> None:
    store, service, adapter = _make_adapter(reflection_interval_sessions=5)
    now = datetime(2026, 3, 27, tzinfo=timezone.utc)
    for index in range(5):
        _put_summary(
            store,
            record_id=f"summary-{index}",
            updated_at=(now - timedelta(days=index)).isoformat(),
            summary_text="I prefer dark mode.",
            topic_keywords=["ui"],
        )
    _put_insight(
        store,
        record_id="insight-dark-mode",
        key="insight-preference:mode",
        title="Stable preference: mode",
        content="Across recent sessions, the user consistently prefers dark mode.",
        tags=["meta_insight", "stable_preference", "dark mode"],
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

    updated = adapter._boost_stable_preferences(  # noqa: SLF001
        session_summaries=adapter._list_reflection_window_summaries()  # noqa: SLF001
    )

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
            limit=10,
        )
    )
    assert updated == 0
    assert len(preferences) == 1
    assert preferences[0].confidence == 0.4
    boosted_insight = next(
        record for record in insights if record.key == "insight-preference:mode"
    )
    assert "boosted_record_key" not in boosted_insight.meta
    assert boosted_insight.meta == {}


def test_boost_stable_preferences_does_not_promote_from_summary_text() -> None:
    store, service, adapter = _make_adapter(reflection_interval_sessions=5)
    now = datetime(2026, 3, 27, tzinfo=timezone.utc)
    for index in range(5):
        _put_summary(
            store,
            record_id=f"summary-{index}",
            updated_at=(now - timedelta(days=index)).isoformat(),
            summary_text="I prefer concise summaries.",
            topic_keywords=["writing"],
        )
    _put_insight(
        store,
        record_id="insight-concise",
        key="insight-preference:summaries",
        title="Stable preference: summaries",
        content=(
            "Across recent sessions, the user consistently prefers concise summaries."
        ),
        tags=["meta_insight", "stable_preference", "concise summaries"],
    )

    updated = adapter._boost_stable_preferences(  # noqa: SLF001
        session_summaries=adapter._list_reflection_window_summaries()  # noqa: SLF001
    )

    preferences = service.list(
        ListQueryOptions(
            scopes=["agent:reflection-agent"],
            types=["user_preference"],
            limit=10,
        )
    )
    assert updated == 0
    assert preferences == []
    assert store.get("insight-concise").is_deleted is False


def test_boost_stable_preferences_respects_cooldown() -> None:
    store, service, adapter = _make_adapter(reflection_interval_sessions=5)
    now = datetime.now(timezone.utc)
    for index in range(5):
        _put_summary(
            store,
            record_id=f"summary-{index}",
            updated_at=(now - timedelta(days=index)).isoformat(),
            summary_text="I prefer dark mode.",
            topic_keywords=["ui"],
        )
    _put_insight(
        store,
        record_id="insight-dark-mode",
        key="insight-preference:mode",
        title="Stable preference: mode",
        content="Across recent sessions, the user consistently prefers dark mode.",
        tags=["meta_insight", "stable_preference", "dark mode"],
        meta={"boosted_at": now.isoformat(), "boosted_record_key": "pref:dark-mode"},
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

    updated = adapter._boost_stable_preferences(  # noqa: SLF001
        session_summaries=adapter._list_reflection_window_summaries()  # noqa: SLF001
    )

    preference = service.list(
        ListQueryOptions(
            scopes=["agent:reflection-agent"],
            types=["user_preference"],
            limit=10,
        )
    )[0]
    assert updated == 0
    assert preference.confidence == 0.4


def test_maybe_run_reflection_keeps_int_return_and_honors_promotion_flag() -> None:
    now = datetime(2026, 3, 27, tzinfo=timezone.utc)
    disabled_store, disabled_service, disabled_adapter = _make_adapter(
        reflection_interval_sessions=5,
        promotion_enabled=False,
    )
    for index in range(5):
        _put_summary(
            disabled_store,
            record_id=f"summary-disabled-{index}",
            updated_at=(now - timedelta(days=index)).isoformat(),
            summary_text="I prefer dark mode. Do not use ad hoc sleeps in tests.",
            corrections=["Do not use ad hoc sleeps in tests."],
            topic_keywords=["ui", "tests"],
        )

    written = disabled_adapter._maybe_run_reflection()  # noqa: SLF001

    assert isinstance(written, int)
    assert written >= 1
    assert (
        disabled_service.list(
            ListQueryOptions(
                scopes=["agent:reflection-agent"],
                types=["correction"],
                limit=10,
            )
        )
        == []
    )
    assert (
        disabled_service.list(
            ListQueryOptions(
                scopes=["agent:reflection-agent"],
                types=["user_preference"],
                limit=10,
            )
        )
        == []
    )


def test_recount_uses_recent_reflection_window_only() -> None:
    store, service, adapter = _make_adapter(reflection_interval_sessions=3)
    now = datetime(2026, 3, 27, tzinfo=timezone.utc)
    for index in range(12):
        prefers_dark = index < 5
        _put_summary(
            store,
            record_id=f"summary-{index}",
            updated_at=(now - timedelta(days=(11 - index))).isoformat(),
            summary_text="I prefer dark mode."
            if prefers_dark
            else "Nothing new today.",
            topic_keywords=["ui"] if prefers_dark else ["misc"],
        )
    _put_insight(
        store,
        record_id="insight-dark-mode",
        key="insight-preference:mode",
        title="Stable preference: mode",
        content="Across recent sessions, the user consistently prefers dark mode.",
        tags=["meta_insight", "stable_preference", "dark mode"],
    )

    updated = adapter._boost_stable_preferences(  # noqa: SLF001
        session_summaries=adapter._list_reflection_window_summaries()  # noqa: SLF001
    )

    preferences = service.list(
        ListQueryOptions(
            scopes=["agent:reflection-agent"],
            types=["user_preference"],
            limit=10,
        )
    )
    assert updated == 0
    assert preferences == []
