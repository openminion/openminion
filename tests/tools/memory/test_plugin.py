from __future__ import annotations

from pathlib import Path

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.memory.runtime.promotion import PromotionPolicy
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.memory import REGISTRAR


def _memory_service(tmp_path: Path) -> MemoryService:
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(SQLiteMemoryStore(tmp_path / "memory.db"), sink=sink)
    return MemoryService(store=store, policy=PromotionPolicy())


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    return registry


def test_memory_tools_write_search_and_forget_round_trip(tmp_path: Path) -> None:
    service = _memory_service(tmp_path)
    registry = _registry()
    context = ToolExecutionContext(
        channel="console",
        target="cli-chat",
        session_id="sess-memory-tools",
        metadata={},
        memory_service=service,
    )

    write_result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.write",
                arguments={
                    "scope": "session:sess-memory-tools",
                    "record_type": "fact",
                    "key": "preferred_database",
                    "title": "Preferred database",
                    "content": {"value": "sqlite"},
                    "tags": ["db"],
                },
                id="write-1",
                source="test",
            )
        ],
        context=context,
    ).results[0]

    assert write_result.ok
    record_id = str(write_result.data["record_id"])

    search_result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.search",
                arguments={
                    "query": "sqlite",
                    "scopes": ["session:sess-memory-tools"],
                    "types": ["fact"],
                    "limit": 5,
                },
                id="search-1",
                source="test",
            )
        ],
        context=context,
    ).results[0]

    assert search_result.ok
    assert search_result.data["count"] == 1
    assert search_result.data["records"][0]["id"] == record_id
    assert search_result.data["records"][0]["content"] == {"value": "sqlite"}

    correction_result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.write",
                arguments={
                    "scope": "session:sess-memory-tools",
                    "record_type": "fact",
                    "key": "fact:preferred_database",
                    "title": "Preferred database",
                    "content": {"value": "postgres"},
                },
                id="write-2",
                source="test",
            )
        ],
        context=context,
    ).results[0]

    assert correction_result.ok
    corrected = service.search(
        SearchQueryOptions(
            query="postgres",
            scopes=["session:sess-memory-tools"],
            types=["fact"],
            limit=5,
        )
    )
    assert len(corrected) == 1
    assert corrected[0].key == "fact:preferred_database"

    forget_result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.forget",
                arguments={"record_id": record_id},
                id="forget-1",
                source="test",
            )
        ],
        context=context,
    ).results[0]

    assert forget_result.ok
    assert forget_result.data["deleted"] is True
    assert (
        service.search(
            SearchQueryOptions(
                query="sqlite",
                scopes=["session:sess-memory-tools"],
                types=["fact"],
                limit=5,
            )
        )
        == []
    )


def test_brain_tool_adapter_preserves_memory_service(tmp_path: Path) -> None:
    service = _memory_service(tmp_path)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=_registry(),
        memory_service=MemctlAdapter(service, agent_id="memory-agent"),
        agent_id="memory-agent",
    )

    result = adapter.execute(
        command={
            "tool_name": "memory.write",
            "args": {
                "scope": "agent:memory-agent",
                "record_type": "fact",
                "key": "fact:preferred_database",
                "title": "Preferred database",
                "content": "sqlite",
            },
            "inputs": {"permission_mode": "bypass"},
        },
        session_id="memory-session",
        trace_id="memory-trace",
    )

    assert result["status"] == "success"
    records = service.search(
        SearchQueryOptions(
            query="sqlite",
            scopes=["agent:memory-agent"],
            types=["fact"],
            limit=1,
        )
    )
    assert len(records) == 1


def test_memory_tools_require_explicit_runtime_service(tmp_path: Path) -> None:
    del tmp_path
    registry = _registry()
    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.search",
                arguments={
                    "query": "sqlite",
                    "scopes": ["session:sess-memory-tools"],
                    "limit": 5,
                },
                id="search-no-service",
                source="test",
            )
        ],
        context=ToolExecutionContext(channel="console", target="cli-chat", metadata={}),
    ).results[0]

    assert result.ok is False
    assert result.data["error_code"] == "DEPENDENCY_MISSING"
    assert result.data["details"]["reason_code"] == "memory_service_unavailable"


def test_memory_tools_do_not_accept_metadata_smuggled_service(tmp_path: Path) -> None:
    registry = _registry()
    result = registry.execute_calls(
        [
            ProviderToolCall(
                name="memory.write",
                arguments={
                    "scope": "session:sess-memory-tools",
                    "record_type": "fact",
                    "key": "fact:ignored_smuggle",
                    "title": "Ignored smuggle",
                    "content": "value",
                },
                id="write-smuggle",
                source="test",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="cli-chat",
            metadata={"memory_service": str(tmp_path / "not-real")},
        ),
    ).results[0]

    assert result.ok is False
    assert result.data["error_code"] == "DEPENDENCY_MISSING"
    assert result.data["details"]["reason_code"] == "memory_service_unavailable"
