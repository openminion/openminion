from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import ListQueryOptions, SearchQueryOptions
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.diagnostics.events import emit_memory_operation
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _seed_store(store: InMemoryMemoryStore) -> None:
    now = datetime.now(timezone.utc).isoformat()
    store.put(
        MemoryRecord(
            id="mem-1",
            scope="session:sess-memory",
            type="fact",
            content="Aurora rollback plan",
            created_at=now,
            updated_at=now,
            meta={"bm25_score": 0.9},
        )
    )
    store.put(
        MemoryRecord(
            id="mem-2",
            scope="session:sess-memory",
            type="fact",
            content="Aurora deploy checklist",
            created_at=now,
            updated_at=now,
            meta={"bm25_score": 0.2},
        )
    )


def test_memory_service_emits_query_rerank_and_fallback(tmp_path: Path) -> None:
    telemetry = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(telemetry)
    store = InMemoryMemoryStore()
    _seed_store(store)
    service = MemoryService(store=store, telemetryctl=ctl)
    service.set_telemetry_context(session_id="sess-memory", turn_id="turn-1")

    listed = service.list(ListQueryOptions(scopes=["session:sess-memory"], limit=5))
    searched = service.search(
        SearchQueryOptions(
            query="aurora rollback",
            scopes=["session:sess-memory"],
            limit=5,
        )
    )
    assert listed
    assert searched

    vector = MagicMock()
    vector.search.return_value = [("mem-1", 0.7, {}), ("mem-2", 0.3, {})]
    service.set_vector_adapter(vector)
    reranked = service.search_semantic(
        query="aurora rollback",
        scopes=["session:sess-memory"],
        limit=2,
    )
    assert reranked

    service.set_vector_adapter(None)
    fallback = service.search_semantic(
        query="aurora deploy",
        scopes=["session:sess-memory"],
        limit=2,
    )
    assert fallback

    summary = _run(telemetry.get_module_summary("sess-memory"))
    stats = summary["openminion-memory"]
    assert stats["operation_counts"]["query"] >= 3
    assert stats["operation_counts"]["rerank"] == 1
    assert stats["operation_counts"]["fallback"] == 1
    assert stats["custom_counter_sums"]["returned_items"] >= 1.0
    assert stats["custom_counter_sums"]["latency_bucket_ms"] >= 0.0
    assert stats["custom_counter_sums"]["token_estimate"] >= 1.0
    _run(telemetry.close())


def test_memory_helper_rejects_unknown_operation_and_absent_adapter() -> None:
    assert (
        emit_memory_operation(
            telemetryctl=None,
            session_id="sess-memory-invalid",
            turn_id="turn-1",
            operation="",
        )
        is False
    )
