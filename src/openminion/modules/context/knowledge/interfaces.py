from typing import ClassVar, Protocol, runtime_checkable

from .contracts import KNOWLEDGE_GRAPH_CONTRACT_VERSION
from .models import (
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

KNOWLEDGE_GRAPH_INTERFACE_VERSION = KNOWLEDGE_GRAPH_CONTRACT_VERSION


@runtime_checkable
class KnowledgeGraphSource(Protocol):
    """A graph-context source that can register into a knowledge-graph layer.

    Implementations advertise their layer/tags/capabilities and expose
    capability-gated read methods. Methods whose capabilities are not
    advertised may raise :class:`UnsupportedCapabilityError`.
    """

    contract_version: ClassVar[str] = KNOWLEDGE_GRAPH_INTERFACE_VERSION

    @property
    def name(self) -> str: ...

    @property
    def layer(self) -> str: ...

    @property
    def tags(self) -> tuple[str, ...]: ...

    @property
    def capabilities(self) -> KnowledgeGraphCapabilities: ...

    def health(self) -> KnowledgeGraphHealth: ...

    def query(self, request: GraphQueryRequest) -> GraphQueryResult: ...

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult: ...

    def path(self, request: GraphPathRequest) -> GraphPathResult: ...

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult: ...

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult: ...


# Method names that a provider may decline when the matching capability is
# not advertised. Runtime should consult this mapping before dispatch.
CAPABILITY_METHOD_MAP: dict[str, str] = {
    "query": "query",
    "neighborhood": "neighborhood",
    "path": "path",
    "explain": "explain",
    "refresh": "refresh",
}
