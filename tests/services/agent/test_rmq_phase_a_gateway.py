from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _make_adapter(*, retrieve_ctl: object | None = None) -> MemoryServiceGatewayAdapter:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(
        service,
        agent_id="rmq-a-agent",
        retrieve_ctl=retrieve_ctl,
    )


def _pipeline(adapter: MemoryServiceGatewayAdapter):
    return adapter._pipeline  # noqa: SLF001


def test_apply_recency_boost_prefers_fresh_over_stale() -> None:
    adapter = _make_adapter()
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    items = [
        {"text": "old", "score": 0.5, "created_at": old_iso, "meta": {}},
        {"text": "fresh", "score": 0.5, "created_at": now_iso, "meta": {}},
        {"text": "missing-date", "score": 0.5, "created_at": None, "meta": {}},
    ]

    ranked = _pipeline(adapter)._apply_recency_boost(  # noqa: SLF001
        items,
        decay_halflife_days=30,
        recency_weight=0.7,
    )
    scores = {item["text"]: float(item["score"]) for item in ranked}
    assert scores["fresh"] > scores["old"]
    assert abs(scores["missing-date"] - scores["fresh"]) < 1e-6


def test_apply_feedback_boost_is_capped_and_clamped() -> None:
    adapter = _make_adapter()
    items = [
        {
            "text": "boosted",
            "score": 0.6,
            "meta": {"hit_count": 10, "feedback_score": 0.0},
        },
        {
            "text": "unchanged",
            "score": 0.6,
            "meta": {"hit_count": 0, "feedback_score": 0.0},
        },
    ]

    ranked = _pipeline(adapter)._apply_feedback_boost(items, max_boost=0.2)  # noqa: SLF001
    scores = {item["text"]: float(item["score"]) for item in ranked}
    assert abs(scores["boosted"] - 0.8) < 1e-6
    assert abs(scores["unchanged"] - 0.6) < 1e-6
    assert max(scores.values()) <= 1.0


def test_apply_feedback_boost_ignores_feedback_score_without_hits() -> None:
    adapter = _make_adapter()
    items = [
        {
            "text": "reuse-backed",
            "score": 0.6,
            "meta": {"hit_count": 10, "feedback_score": 0.0},
        },
        {
            "text": "outcome-only",
            "score": 0.6,
            "meta": {"hit_count": 0, "feedback_score": 1.0},
        },
    ]

    ranked = _pipeline(adapter)._apply_feedback_boost(items, max_boost=0.2)  # noqa: SLF001
    scores = {item["text"]: float(item["score"]) for item in ranked}

    assert abs(scores["reuse-backed"] - 0.8) < 1e-6
    assert abs(scores["outcome-only"] - 0.6) < 1e-6


def test_build_retrieval_filters_shape() -> None:
    adapter = _make_adapter()
    no_project = _pipeline(adapter)._build_retrieval_filters(  # noqa: SLF001
        session_id="sess-a",
        agent_id="agent-a",
        project_id=None,
        source_types=["mem", "episode"],
        time_window_hours=168,
    )
    with_project = _pipeline(adapter)._build_retrieval_filters(  # noqa: SLF001
        session_id="sess-a",
        agent_id="agent-a",
        project_id="project-a",
        source_types=["doc", "skill"],
        time_window_hours=None,
    )

    assert no_project.scope_keys == ["session:sess-a", "agent:agent-a"]
    assert with_project.scope_keys == [
        "session:sess-a",
        "agent:agent-a",
        "project:project-a",
    ]
    assert no_project.types == ["mem", "episode"]
    assert with_project.time_window_hours is None


def test_retrieve_split_isolates_failures_per_lane() -> None:
    retrieve_ctl = Mock(name="retrieve_ctl")
    retrieve_ctl.retrieve.side_effect = [
        RuntimeError("conv failed"),
        [{"text": "knowledge", "meta": {"unit_id": "u-k"}}],
    ]
    adapter = _make_adapter(retrieve_ctl=retrieve_ctl)
    merged, counts = _pipeline(adapter)._retrieve_split(  # noqa: SLF001
        retrieve_ctl,
        query="query",
        session_id="sess-a",
        agent_id="agent-a",
        project_id=None,
        k_conversational=3,
        k_knowledge=3,
    )

    assert counts["conversational"] == 0
    assert counts["knowledge"] == 1
    assert merged == [{"text": "knowledge", "meta": {"unit_id": "u-k"}}]


def test_pipeline_order_split_then_boosts() -> None:
    retrieve_ctl = Mock(name="retrieve_ctl")
    retrieve_ctl.retrieve.return_value = [{"text": "x", "meta": {"unit_id": "u1"}}]
    adapter = _make_adapter(retrieve_ctl=retrieve_ctl)
    adapter._config = SimpleNamespace(  # noqa: SLF001
        defaults=SimpleNamespace(
            k_conversational=1,
            k_knowledge=1,
            decay_halflife_days=30,
            recency_weight=0.3,
            mmr_enabled=False,
            mmr_lambda=0.6,
        )
    )
    _pipeline(adapter)._config = adapter._config  # noqa: SLF001

    order: list[str] = []
    orig_split = _pipeline(adapter)._retrieve_split  # noqa: SLF001
    orig_recency = _pipeline(adapter)._apply_recency_boost  # noqa: SLF001
    orig_feedback = _pipeline(adapter)._apply_feedback_boost  # noqa: SLF001

    def _split(*args, **kwargs):  # type: ignore[no-untyped-def]
        order.append("split")
        return orig_split(*args, **kwargs)

    def _recency(*args, **kwargs):  # type: ignore[no-untyped-def]
        order.append("recency")
        return orig_recency(*args, **kwargs)

    def _feedback(*args, **kwargs):  # type: ignore[no-untyped-def]
        order.append("feedback")
        return orig_feedback(*args, **kwargs)

    _pipeline(adapter)._retrieve_split = _split  # type: ignore[method-assign] # noqa: SLF001
    _pipeline(adapter)._apply_recency_boost = _recency  # type: ignore[method-assign] # noqa: SLF001
    _pipeline(adapter)._apply_feedback_boost = _feedback  # type: ignore[method-assign] # noqa: SLF001

    adapter.build_retrieval_context_with_metadata(
        session_id="sess-a", user_message="hello"
    )
    assert order[:3] == ["split", "recency", "feedback"]
