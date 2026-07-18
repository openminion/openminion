from __future__ import annotations

import ast
from pathlib import Path
import sys

from sophiagraph import (
    ArtifactRef,
    MemoryCandidate,
    MemoryNamespace,
    SophiaGraphMemoryStore,
    WorkbenchActionExecutionContext,
    WorkbenchActionRequest,
    execute_workbench_action,
    preview_workbench_execution,
    workbench_action_status,
)

from openminion.modules.context.knowledge.config import KnowledgeGraphProviderConfig
from openminion.modules.context.knowledge.constants import (
    CAPABILITY_QUERY,
    LAYER_THIRD_BRAIN,
)
from openminion.modules.context.knowledge.models import (
    GraphExplainRequest,
    GraphExplainResult,
    GraphNeighborhoodRequest,
    GraphPathRequest,
    GraphPathResult,
    GraphQueryRequest,
    GraphQueryResult,
    GraphRefreshRequest,
    GraphRefreshResult,
    KnowledgeGraphCapabilities,
    KnowledgeGraphHealth,
)
from openminion.modules.context.knowledge.registry import KnowledgeGraphRegistry
from openminion.modules.memory.backends.config import (
    DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER,
)


def test_openminion_can_execute_sophiagraph_workbench_action_directly() -> None:
    namespace = MemoryNamespace(agent_id="openminion", graph_id="main")
    store = SophiaGraphMemoryStore()
    store.put_candidate(
        MemoryCandidate(
            candidate_id="candidate:handoff",
            session_id="session:handoff",
            proposed_scope="agent:openminion",
            type="fact",
            title="Workbench handoff",
            content={"text": "OpenMinion can use public Sophiagraph actions."},
            evidence_refs=(
                ArtifactRef(
                    ref="artifact://handoff-source",
                    mime="text/plain",
                    sha256="a" * 64,
                    size_bytes=42,
                ),
            ),
            status="proposed",
            namespace=namespace,
            source_class="user_input",
            created_at="2026-07-18T10:00:00+00:00",
            updated_at="2026-07-18T10:00:00+00:00",
        )
    )
    request = WorkbenchActionRequest(
        action="approve_candidate",
        target_id="candidate:candidate:handoff",
        actor_id="openminion-local-operator",
        workspace_id="workspace:openminion",
        payload_kind="candidate",
        payload={"expected_updated_at": "2026-07-18T10:00:00+00:00"},
    )
    context = WorkbenchActionExecutionContext(
        action_id="openminion:approve:handoff",
        request_id="request:openminion:approve:handoff",
        principal_id="openminion-local-operator",
        workspace_id="workspace:openminion",
        scope="agent:openminion",
        namespace=namespace,
    )

    preview = preview_workbench_execution(request, context)
    result = execute_workbench_action(store, request, context)
    replay = execute_workbench_action(store, request, context)
    entry = workbench_action_status(
        store,
        action_id=context.action_id,
        scope=context.scope,
        namespace=namespace,
    )

    assert preview.outcome == "preview_only"
    assert result.outcome == "applied"
    assert result.reason_code == "applied"
    assert "action_journal:openminion:approve:handoff" in result.audit_refs
    assert replay.to_dict() == result.to_dict()
    assert entry is not None
    assert entry.result is not None
    assert entry.result.to_dict() == result.to_dict()
    assert store.get_candidate("candidate:handoff").status == "approved"
    assert "sophiagraph.server" not in sys.modules


def test_graph_viewer_layer_does_not_replace_durable_memory_backend() -> None:
    registry = KnowledgeGraphRegistry()
    registry.register("graphfakos", lambda **_kwargs: _ThirdPartyViewerSource())
    cfg = KnowledgeGraphProviderConfig(
        name="viewer",
        provider="graphfakos",
        required_capabilities=(CAPABILITY_QUERY,),
    )

    source = registry.instantiate(cfg, layer=LAYER_THIRD_BRAIN)

    assert source.layer == LAYER_THIRD_BRAIN
    assert source.provider == "graphfakos"
    assert source.capabilities.supports(CAPABILITY_QUERY)
    assert DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER == "sophiagraph"


def test_workbench_action_fixture_uses_public_sophiagraph_imports_only() -> None:
    tree = ast.parse(Path(__file__).read_text())
    modules = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "sophiagraph.server" not in modules
    assert "sophiagraph.workbench_actions" not in modules
    assert "sophiagraph.workbench" not in modules
    assert "sophiagraph.models" not in modules


class _ThirdPartyViewerSource:
    @property
    def name(self) -> str:
        return "viewer"

    @property
    def provider(self) -> str:
        return "graphfakos"

    @property
    def layer(self) -> str:
        return LAYER_THIRD_BRAIN

    @property
    def tags(self) -> tuple[str, ...]:
        return ()

    @property
    def capabilities(self) -> KnowledgeGraphCapabilities:
        return KnowledgeGraphCapabilities(frozenset({CAPABILITY_QUERY}))

    def health(self) -> KnowledgeGraphHealth:
        return KnowledgeGraphHealth(
            provider=self.provider,
            layer=self.layer,
            ok=True,
        )

    def query(self, request: GraphQueryRequest) -> GraphQueryResult:
        return GraphQueryResult(provider=self.provider, layer=self.layer)

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult:
        return GraphQueryResult(provider=self.provider, layer=self.layer)

    def path(self, request: GraphPathRequest) -> GraphPathResult:
        return GraphPathResult(provider=self.provider)

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult:
        return GraphExplainResult(provider=self.provider)

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult:
        return GraphRefreshResult(provider=self.provider, refreshed=False)
