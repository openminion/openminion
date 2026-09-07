from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.brain.adapters.memory.runtime import MemctlAdapter
from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.brain.loop.tools.messages import action_result_to_tool_message
from openminion.modules.brain.schemas import ActionResult
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
from openminion.tools.memory.plugin import MemorySearchArgs


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


def test_memory_search_uses_active_scope_and_exposes_first_record_to_model(
    tmp_path: Path,
) -> None:
    nonce = "memory-model-visible-proof-7319"
    service = _memory_service(tmp_path)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=_registry(),
        memory_service=MemctlAdapter(service, agent_id="memory-agent"),
        agent_id="memory-agent",
    )
    service.write_record(
        scope="agent:memory-agent",
        record_type="procedure",
        key="procedure:model-visible-proof",
        title="Model-visible procedure",
        content={
            "steps": [f"Write the remembered marker {nonce}", "Verify the marker"],
            "proof_nonce": nonce,
        },
    )

    result = adapter.execute(
        command={
            "tool_name": "memory.search",
            "args": {"query": "model-visible procedure", "types": ["procedure"]},
            "inputs": {"permission_mode": "bypass"},
        },
        session_id="memory-session",
        trace_id="memory-trace",
    )

    assert MemorySearchArgs(query="proof").scopes == []
    assert MemorySearchArgs(query="proof", scopes=[]).scopes == []
    assert result["status"] == "success"
    output = result["outputs"]
    assert output["data"]["scopes"] == ["agent:memory-agent"]
    assert output["data"]["records"][0]["content"]["proof_nonce"] == nonce
    model_projection = json.loads(output["content"])["records"][0]
    assert set(model_projection) == {"content", "id", "title", "type"}
    assert model_projection["content"]["proof_nonce"] == nonce

    message = action_result_to_tool_message(
        "search-call",
        "memory.search",
        ActionResult(command_id="search-command", **result),
    )
    assert nonce in message.content


def test_memory_search_chunks_large_first_record_without_losing_structure(
    tmp_path: Path,
) -> None:
    service = _memory_service(tmp_path)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=_registry(),
        memory_service=MemctlAdapter(service, agent_id="memory-agent"),
        agent_id="memory-agent",
    )
    service.write_record(
        scope="agent:memory-agent",
        record_type="procedure",
        key="procedure:large-model-visible-proof",
        title="Large model-visible procedure",
        content={
            "steps": ["Write the detailed report: " + "detail " * 240],
            "tools": ["file.write", "file.read"],
        },
    )

    result = adapter.execute(
        command={
            "tool_name": "memory.search",
            "args": {"query": "large model-visible procedure"},
            "inputs": {"permission_mode": "bypass"},
        },
        session_id="memory-session",
        trace_id="memory-trace",
    )

    assert result["status"] == "success"
    output = result["outputs"]
    assert output["data"]["model_content_complete"] is True
    projection = json.loads("".join(output["data"]["model_content_chunks"]))
    first = projection["records"][0]
    assert first["content"]["tools"] == ["file.write", "file.read"]
    assert first["content"]["steps"][0].endswith("detail ")

    message = action_result_to_tool_message(
        "search-call",
        "memory.search",
        ActionResult(command_id="search-command", **result),
    )
    payload = json.loads(message.content)
    chunks = payload["outputs"]["data"]["model_content_chunks"]
    assert json.loads("".join(chunks))["records"][0]["content"]["tools"] == [
        "file.write",
        "file.read",
    ]


def test_memory_search_marks_above_limit_projection_incomplete(tmp_path: Path) -> None:
    service = _memory_service(tmp_path)
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=_registry(),
        memory_service=MemctlAdapter(service, agent_id="memory-agent"),
        agent_id="memory-agent",
    )
    service.write_record(
        scope="agent:memory-agent",
        record_type="procedure",
        key="procedure:above-model-limit",
        title="Above model-visible limit",
        content={
            "steps": ["Write the detailed report: " + "detail " * 1400],
            "tools": ["file.write", "file.read"],
        },
    )

    result = adapter.execute(
        command={
            "tool_name": "memory.search",
            "args": {"query": "above model-visible limit"},
            "inputs": {"permission_mode": "bypass"},
        },
        session_id="memory-session",
        trace_id="memory-trace",
    )

    output = result["outputs"]
    assert output["data"]["model_content_complete"] is False
    assert output["data"]["model_content_size"] > 8000
    assert "model_content_chunks" not in output["data"]
    assert "exceeds the 8000-character model-visible limit" in output["content"]


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
