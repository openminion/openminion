from typing import Any, Protocol, runtime_checkable

from .schemas import (
    ArtifactDigest,
    BuildPackRequest,
    ContextPack,
    EvidenceItem,
    FactRecord,
    IdentitySnippet,
    MemoryCard,
    RecentSessionArtifactRef,
    SessionSlice,
)


CONTEXT_CLIENT_INTERFACE_VERSION = "v1"
CONTEXT_CONTRACT_VERSION = "v1"
SESSION_CONTRACT_VERSION = "v1"
MEMORY_CONTRACT_VERSION = "v1"
BRAIN_CONTRACT_VERSION = "v1"


@runtime_checkable
class IdentityClient(Protocol):
    contract_version: str

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet: ...


@runtime_checkable
class SessionClient(Protocol):
    contract_version: str

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: dict[str, int],
    ) -> SessionSlice: ...


@runtime_checkable
class MemoryClient(Protocol):
    contract_version: str

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[FactRecord]: ...

    def query_memory_cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]: ...

    def recall_session_start_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        turn_index: int,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]: ...

    def recall_mid_session_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        turn_index: int,
        intent_ids: list[str],
        intent_statuses: list[str],
        latest_user_message: str,
        active_skill_id: str | None,
        resolved_skill_ids: list[str],
        plan_cursor: int,
        plan_step_ids: list[str],
        recent_tool_families: list[str],
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]: ...

    def recall_recent_session_artifacts(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_results: int,
        max_session_age: int,
        mode_name: str | None = None,
    ) -> list[RecentSessionArtifactRef]: ...

    def get_procedure(self, *, procedure_id: str) -> Any | None: ...


@runtime_checkable
class ArtifactClient(Protocol):
    contract_version: str

    def query_digests(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> list[ArtifactDigest]: ...


@runtime_checkable
class SkillClient(Protocol):
    contract_version: str

    def render_snippet(
        self,
        skill_id: str,
        version_hash: str | None,
        purpose: str,
        max_tokens: int,
        mode_name: str | None = None,
    ) -> tuple[str, str]: ...


@runtime_checkable
class CompressClient(Protocol):
    """Interface for consuming compressed summaries from context.compress."""

    contract_version: str

    def get_snapshot(
        self,
        *,
        session_id: str,
        agent_id: str,
        mode_name: str | None = None,
    ) -> str | None:
        """Return compressed summary text for inclusion in summaries bucket, or None."""
        ...


@runtime_checkable
class RlmClient(Protocol):
    """Interface for consuming reinforcement-learned memory from openminion-rlm."""

    contract_version: str

    def get_refresh_summary(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
    ) -> str | None:
        """Return rlm-refreshed retrieval summary text, or None."""
        ...


@runtime_checkable
class VectorClient(Protocol):
    """Interface for semantic vector search from openminion storage."""

    contract_version: str

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]:
        """Search for semantically similar items. Returns (id, score, metadata) tuples."""
        ...


@runtime_checkable
class LlmTelemetrySink(Protocol):
    """C-18: Cache token telemetry writeback from openminion-llm."""

    contract_version: str

    def record_cache_metrics(
        self,
        *,
        session_id: str,
        agent_id: str,
        prompt_cache_key: str,
        cached_tokens: int,
        total_tokens: int,
        provider: str,
    ) -> None: ...


@runtime_checkable
class ContextRetriever(Protocol):
    """Retrieve candidate evidence items for a query."""

    contract_version: str
    name: str

    def retrieve(
        self,
        *,
        session_id: str,
        query: str,
        k: int,
        filters: dict[str, Any],
    ) -> list[EvidenceItem]: ...


@runtime_checkable
class ContextCompressor(Protocol):
    """Compress candidate evidence items under a token budget."""

    contract_version: str
    name: str

    def compress(
        self,
        *,
        query: str,
        items: list[EvidenceItem],
        budget_tokens: int,
    ) -> list[EvidenceItem]: ...


class PluginRegistry:
    """C-21: Registry for retriever/compressor plugins."""

    def __init__(self) -> None:
        self._retrievers: dict[str, ContextRetriever] = {}
        self._compressors: dict[str, ContextCompressor] = {}

    def register_retriever(self, plugin: ContextRetriever) -> None:
        ensure_context_client_compatibility(plugin, client_type="retriever")
        self._retrievers[plugin.name] = plugin

    def register_compressor(self, plugin: ContextCompressor) -> None:
        ensure_context_client_compatibility(plugin, client_type="compressor")
        self._compressors[plugin.name] = plugin

    def get_retriever(self, name: str) -> ContextRetriever | None:
        return self._retrievers.get(name)

    def get_compressor(self, name: str) -> ContextCompressor | None:
        return self._compressors.get(name)

    @property
    def retriever_names(self) -> list[str]:
        return list(self._retrievers)

    @property
    def compressor_names(self) -> list[str]:
        return list(self._compressors)


_REQUIRED_MEMBERS: dict[str, tuple[str, ...]] = {
    "identity": ("contract_version", "render"),
    "session": ("contract_version", "get_slice"),
    "memory": (
        "contract_version",
        "query_facts",
        "query_memory_cards",
        "recall_session_start_memory",
        "recall_mid_session_memory",
        "recall_recent_session_artifacts",
        "get_procedure",
    ),
    "artifact": ("contract_version", "query_digests"),
    "skill": ("contract_version", "render_snippet"),
    "compress": ("contract_version", "get_snapshot"),
    "rlm": ("contract_version", "get_refresh_summary"),
    "llm_telemetry": ("contract_version", "record_cache_metrics"),
    "retriever": ("contract_version", "name", "retrieve"),
    "compressor": ("contract_version", "name", "compress"),
    "vector": ("contract_version", "search"),
}


def ensure_context_client_compatibility(client: Any, *, client_type: str) -> None:
    normalized = str(client_type or "").strip().lower()
    required = _REQUIRED_MEMBERS.get(normalized)
    if required is None:
        raise ValueError(f"unknown client_type: {client_type}")

    missing: list[str] = []
    for name in required:
        if not hasattr(client, name):
            missing.append(name)
            continue
        value = getattr(client, name)
        if name in {"contract_version", "name"}:
            continue
        if not callable(value):
            missing.append(name)
    if missing:
        raise TypeError(
            f"{client.__class__.__name__} is incompatible with context {normalized} contract; missing members: {', '.join(missing)}"
        )

    version = str(getattr(client, "contract_version", "")).strip()
    if version != CONTEXT_CLIENT_INTERFACE_VERSION:
        raise TypeError(
            f"{client.__class__.__name__} has unsupported contract_version={version!r}; expected {CONTEXT_CLIENT_INTERFACE_VERSION!r}"
        )


@runtime_checkable
class SessionContext(Protocol):
    """Session slice operations under the canonical `get_slice` name."""

    contract_version: str

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: dict[str, int],
    ) -> SessionSlice: ...


@runtime_checkable
class DataContext(Protocol):
    """Aggregate query/fetch operations for identity/memory/artifact data."""

    contract_version: str

    def render_identity(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
    ) -> IdentitySnippet: ...

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[FactRecord]: ...

    def query_memory_cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> list[MemoryCard]: ...

    def query_artifact_digests(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> list[ArtifactDigest]: ...


@runtime_checkable
class ContextBuilder(Protocol):
    """Post-reset canonical builder of `ContextPack` from request + budget."""

    contract_version: str

    def build_pack(self, request: BuildPackRequest) -> ContextPack: ...
