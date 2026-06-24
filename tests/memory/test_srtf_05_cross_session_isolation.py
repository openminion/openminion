from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from openminion.modules.brain.adapters.factory import create_context_adapter
from openminion.modules.context.schemas import BuildPackRequest
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


class _SessionStoreWithPath:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path


def _tool_outcome_record(
    record_id: str,
    *,
    outcome: str,
    text: str,
    agent_id: str = "srtf-agent",
) -> MemoryRecord:
    now = datetime.now(timezone.utc).isoformat()
    is_failure = outcome != "success"
    return MemoryRecord(
        id=record_id,
        scope=f"agent:{agent_id}",
        type="tool_outcome",
        title=f"tool_outcome:weather.search:{outcome}",
        content={
            "text": text,
            "tool_name": "weather.search",
            "tool_family": "weather",
            "outcome": outcome,
            "error_code": "NOT_FOUND" if is_failure else None,
        },
        created_at=now,
        updated_at=now,
        tags=[
            "tool_outcome",
            "tool_family:weather",
            f"outcome:{outcome}",
        ],
        meta={
            "source_kind": "tool_outcome",
            "source_negative_outcome": is_failure,
            "source_success_path": not is_failure,
            "source_outcome_status": outcome,
            "source_tool_name": "weather.search",
            "source_tool_family": "weather",
            "source_session_id": "session-a",
        },
        confidence=0.9,
    )


def test_srtf_05_agent_scoped_failure_outcomes_do_not_poison_new_session(
    tmp_path,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    session_db = state_dir / "sessions.db"
    memory_db = state_dir / "memory.db"
    store = SQLiteMemoryStore(memory_db)

    store.put(
        _tool_outcome_record(
            "mem_agent_failure_a",
            outcome="failure",
            text="Unknown tool: weather.search from session A",
        )
    )
    store.put(
        _tool_outcome_record(
            "mem_agent_success_a",
            outcome="success",
            text="weather.search succeeded for metric weather reports.",
        )
    )

    agent_records = store.list(
        ListQueryOptions(
            scopes=["agent:srtf-agent"],
            types=["tool_outcome"],
            limit=10,
        )
    )
    assert {record.id for record in agent_records} == {
        "mem_agent_failure_a",
        "mem_agent_success_a",
    }

    ctx_adapter = create_context_adapter(
        mode="auto",
        session_store=_SessionStoreWithPath(session_db),
    )
    pack = ctx_adapter.service.build_pack(
        BuildPackRequest(
            session_id="session-b",
            agent_id="srtf-agent",
            purpose="act",
            query="weather",
        )
    )

    rendered = "\n".join(segment.content for segment in pack.segments)
    assert "Unknown tool: weather.search from session A" not in rendered
    assert "mem_agent_failure_a" not in pack.context_manifest.memory
    assert "mem_agent_failure_a" not in pack.context_manifest.facts
    assert "weather.search succeeded for metric weather reports." in rendered
    assert "mem_agent_success_a" in pack.context_manifest.memory
