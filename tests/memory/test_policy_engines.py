from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.adapters.factory import create_memory_adapter
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import SearchQueryOptions


def test_memory_service_retrieval_policy_caps_limits_and_records_reason_code() -> None:
    store = MagicMock()
    store.search.return_value = []
    service = MemoryService(
        store=store,
        policy_config={"retrieval": {"max_results": 2}},
    )

    service.search(
        SearchQueryOptions(
            query="orion",
            scopes=["session:s1"],
            limit=10,
        )
    )

    called_options = store.search.call_args.args[0]
    assert called_options.limit == 2
    decisions = service.last_policy_decisions()
    assert decisions
    assert decisions[-1]["reason_code"] == "retrieval_limit_capped"


def test_memory_service_promotion_policy_emits_deterministic_reason_code() -> None:
    store = MagicMock()
    service = MemoryService(store=store)
    candidate = MemoryCandidate(
        candidate_id="cand-1",
        session_id="s1",
        proposed_scope="agent:main",
        type="fact",
        content={"text": "x"},
        source="agent_inferred",
        status="proposed",
    )
    store.candidate_get.return_value = candidate

    with pytest.raises(Exception) as exc_info:  # PromotionDeniedError
        service.promote_candidate("cand-1", "global:all")

    details = getattr(exc_info.value, "details", {})
    assert (
        details.get("reason_code") == "promotion_denied_global_scope_requires_approval"
    )


def test_memory_service_capsule_refresh_policy_is_deterministic() -> None:
    service = MemoryService(store=MagicMock())
    first = service.should_refresh_capsule(
        strategy="refresh_on_write",
        has_cached_capsule=True,
        memory_changed=False,
    )
    second = service.should_refresh_capsule(
        strategy="refresh_on_write",
        has_cached_capsule=True,
        memory_changed=False,
    )

    assert first.allowed is False
    assert second.allowed is False
    assert first.reason_code == second.reason_code == "refresh_skipped_no_change"


def test_create_memory_adapter_uses_retrieval_policy_config_in_mock_backend(
    tmp_path,
) -> None:
    adapter = create_memory_adapter(
        mode="auto",
        db_path=tmp_path / "memory",
        config={
            "store": {"backend": "mock"},
            "retrieval": {"max_results": 1},
        },
    )
    adapter.put_record(
        scope="session:s1",
        record_type="fact",
        title="orion one",
        content={"text": "orion one"},
    )
    adapter.put_record(
        scope="session:s1",
        record_type="fact",
        title="orion two",
        content={"text": "orion two"},
    )

    hits = adapter.query_facts(
        session_id="s1",
        agent_id="main",
        query="orion",
        limit=10,
    )
    assert len(hits) == 1
