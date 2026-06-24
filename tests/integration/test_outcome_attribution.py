from __future__ import annotations


from openminion.base.config import OpenMinionConfig
from openminion.modules.brain.adapters.memory import MemctlAdapter
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def test_context_to_observe_updates_durable_memory_and_live_ranking(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    older = MemoryRecord(
        id="mem_old",
        scope="agent:oa-agent",
        type="fact",
        title="Deploy note old",
        content="The deploy checklist requires a rollback rehearsal.",
        created_at="2026-03-20T00:00:00+00:00",
        updated_at="2026-03-20T00:00:00+00:00",
        meta={"feedback_score": 0.0},
    )
    newer = MemoryRecord(
        id="mem_new",
        scope="agent:oa-agent",
        type="fact",
        title="Deploy note new",
        content="The deploy checklist requires a rollback rehearsal.",
        created_at="2026-03-27T00:00:00+00:00",
        updated_at="2026-03-27T00:00:00+00:00",
        meta={"feedback_score": 0.0},
    )
    store.put(older)
    store.put(newer)

    memory_config = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=tmp_path / "home",
        data_root=tmp_path / "data",
    )
    gateway = MemoryServiceGatewayAdapter(
        service,
        agent_id="oa-agent",
        memory_config=memory_config,
    )
    memctl = MemctlAdapter(service)

    query = "what deploy checklist should I follow?"
    before_hits = service.search(
        SearchQueryOptions(
            query=query,
            scopes=["agent:oa-agent"],
            limit=5,
        )
    )
    before_ranked = [
        item.id
        for item in gateway._rerank_long_term_records(  # noqa: SLF001
            before_hits,
            use_search_scores=True,
        )
    ]
    assert before_ranked[:2] == ["mem_new", "mem_old"]

    memctl.apply_outcome_feedback(
        record_ids=["mem_old"],
        outcome="success",
        command_id="cmd-oa-1",
        observed_at="2026-04-24T00:00:00+00:00",
        feedback_delta=0.6,
    )

    updated = service.get("mem_old")
    assert updated.meta["feedback_score"] == 0.6
    assert updated.meta["last_outcome_status"] == "success"

    after_hits = service.search(
        SearchQueryOptions(
            query=query,
            scopes=["agent:oa-agent"],
            limit=5,
        )
    )
    after_ranked = [
        item.id
        for item in gateway._rerank_long_term_records(  # noqa: SLF001
            after_hits,
            use_search_scores=True,
        )
    ]

    assert after_ranked
    assert after_ranked[:2] == ["mem_old", "mem_new"]
