"""Generic graph tool contracts and runtime wiring."""

from __future__ import annotations

from pathlib import Path

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.context.knowledge import (
    GraphContextItem,
    GraphQueryResult,
    GraphRefreshResult,
    GraphSourceRef,
    LAYER_THIRD_BRAIN,
)
from openminion.modules.context.knowledge.errors import UnknownProviderError
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.framework import derive_manifest, derive_tool_specs
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.graph import GRAPH_FAMILY, REGISTRAR
from openminion.tools.graph.interfaces import (
    ALL_GRAPH_TOOLS,
    TOOL_GRAPH_NEIGHBORHOOD,
    TOOL_GRAPH_QUERY,
    TOOL_GRAPH_REFRESH,
)


class _GraphService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, tuple[str, ...]]] = []

    def query(self, request, *, provider_names=(), layer=None):
        del layer
        providers = tuple(provider_names)
        self.calls.append(("query", request, providers))
        if providers == ("missing",):
            raise UnknownProviderError(
                "No active knowledge-graph source named 'missing'",
                details={"name": "missing", "active": ["vault_graph"]},
            )
        return (
            GraphQueryResult(
                provider=providers[0],
                layer=LAYER_THIRD_BRAIN,
                tags=("document_graph",),
                items=(
                    GraphContextItem(
                        provider=providers[0],
                        source_graph_id="ovga-vault",
                        node_or_edge_id="note-hub",
                        source_ref=GraphSourceRef(path="Hub.md"),
                        snippet="Hub",
                    ),
                ),
            ),
        )

    def neighborhood(self, request, *, provider_names=(), layer=None):
        del layer
        providers = tuple(provider_names)
        self.calls.append(("neighborhood", request, providers))
        return self.query(request, provider_names=providers)

    def refresh(self, request, *, provider_names=(), layer=None):
        del layer
        providers = tuple(provider_names)
        self.calls.append(("refresh", request, providers))
        return (
            GraphRefreshResult(
                provider=providers[0],
                layer=LAYER_THIRD_BRAIN,
                ok=True,
                counts={"changed": 1},
            ),
        )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    return registry


def _call(
    registry: ToolRegistry,
    context: ToolExecutionContext,
    name: str,
    arguments: dict[str, object],
):
    return registry.execute_calls(
        [
            ProviderToolCall(
                name=name,
                arguments=arguments,
                id=f"{name}-1",
                source="test",
            )
        ],
        context=context,
    ).results[0]


def test_graph_family_manifest_and_runtime_metadata_match() -> None:
    registry = _registry()
    tools = registry.list()

    assert tuple(tools) == ALL_GRAPH_TOOLS
    assert derive_manifest(GRAPH_FAMILY) == REGISTRAR.get_manifest(None)
    assert (
        tuple(tool.name for tool in derive_tool_specs(GRAPH_FAMILY)) == ALL_GRAPH_TOOLS
    )
    assert tools[TOOL_GRAPH_QUERY].min_scope == "READ_ONLY"
    assert tools[TOOL_GRAPH_QUERY].dangerous is False
    assert tools[TOOL_GRAPH_QUERY].idempotent is True
    assert tools[TOOL_GRAPH_NEIGHBORHOOD].idempotent is True
    assert tools[TOOL_GRAPH_REFRESH].min_scope == "WRITE_SAFE"
    assert tools[TOOL_GRAPH_REFRESH].dangerous is True
    assert tools[TOOL_GRAPH_REFRESH].idempotent is False
    assert tools[TOOL_GRAPH_REFRESH].block_under_readonly is True


def test_graph_query_and_neighborhood_target_one_required_source() -> None:
    service = _GraphService()
    context = ToolExecutionContext(
        channel="console",
        target="tests",
        knowledge_graph_service=service,
    )
    registry = _registry()

    query = _call(
        registry,
        context,
        TOOL_GRAPH_QUERY,
        {"source": "vault_graph", "query": "OVGA-MARKER", "limit": 5},
    )
    neighborhood = _call(
        registry,
        context,
        TOOL_GRAPH_NEIGHBORHOOD,
        {
            "source": "vault_graph",
            "entity_id": "note-hub",
            "depth": 2,
            "limit": 8,
        },
    )

    assert query.ok is True
    assert query.data["results"][0]["items"][0]["source_ref"]["path"] == "Hub.md"
    assert neighborhood.ok is True
    assert service.calls[0][2] == ("vault_graph",)
    assert service.calls[1][0] == "neighborhood"


def test_graph_tools_reject_provider_configuration_overrides() -> None:
    context = ToolExecutionContext(
        channel="console",
        target="tests",
        knowledge_graph_service=_GraphService(),
    )

    result = _call(
        _registry(),
        context,
        TOOL_GRAPH_REFRESH,
        {
            "source": "vault_graph",
            "root": "/tmp/other",
            "mode": "full",
        },
    )

    assert result.ok is False
    assert result.data["error_code"] == "invalid_arguments"


def test_graph_refresh_requires_confirmation_and_calls_service_once() -> None:
    service = _GraphService()
    adapter = ToolAdapter(
        workspace_root=Path.cwd(),
        runtime_registry=_registry(),
        knowledge_graph_service=service,
        policy={"tools": {"allow_prefix": ["graph."]}},
    )
    denied = adapter.execute(
        command={
            "tool_name": TOOL_GRAPH_REFRESH,
            "args": {"source": "vault_graph"},
            "inputs": {"permission_mode": "ask"},
        },
        session_id="graph-session",
        trace_id="graph-trace",
    )
    confirmed = adapter.execute(
        command={
            "tool_name": TOOL_GRAPH_REFRESH,
            "args": {"source": "vault_graph"},
            "inputs": {
                "confirmation_grant_id": "ovga-graph-refresh",
                "confirmation_source": "policy_replay",
            },
        },
        session_id="graph-session",
        trace_id="graph-trace",
    )

    assert denied["error"]["code"] == "CONFIRM_REQUIRED"
    assert confirmed["status"] == "success"
    assert [call[0] for call in service.calls] == ["refresh"]


def test_graph_tools_report_unavailable_service_and_typed_provider_error() -> None:
    unavailable = _call(
        _registry(),
        ToolExecutionContext(channel="console", target="tests"),
        TOOL_GRAPH_QUERY,
        {"source": "vault_graph", "query": "marker"},
    )
    unknown = _call(
        _registry(),
        ToolExecutionContext(
            channel="console",
            target="tests",
            knowledge_graph_service=_GraphService(),
        ),
        TOOL_GRAPH_QUERY,
        {"source": "missing", "query": "marker"},
    )

    assert unavailable.data["error_code"] == "DEPENDENCY_MISSING"
    assert unavailable.data["details"]["reason_code"] == "graph_service_unavailable"
    assert unknown.data["error_code"] == "NOT_FOUND"
    assert unknown.data["details"]["graph_error_code"] == "UNKNOWN_PROVIDER"
    assert unknown.data["details"]["name"] == "missing"


def test_brain_tool_adapter_preserves_graph_service(tmp_path: Path) -> None:
    service = _GraphService()
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=_registry(),
        knowledge_graph_service=service,
        agent_id="graph-agent",
        policy={"tools": {"allow_prefix": ["graph."]}},
    )

    result = adapter.execute(
        command={
            "tool_name": TOOL_GRAPH_QUERY,
            "args": {"source": "vault_graph", "query": "marker"},
            "inputs": {"permission_mode": "bypass"},
        },
        session_id="graph-session",
        trace_id="graph-trace",
    )

    assert result["status"] == "success"
    assert service.calls[0][0] == "query"
