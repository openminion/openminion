"""Lower durable-memory backend seam beneath ``MemoryService``."""

from .builtin import (
    BackendMemoryStoreAdapter,
    BuiltinKnowledgeBackend,
    adapt_backend_to_store,
)
from .none import NoneKnowledgeBackend
from .config import (
    DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER,
    KnowledgeBackendConfig,
    resolve_backend_config,
)
from .factory import (
    KnowledgeBackendFactory,
    ResolvedKnowledgeBackendFactory,
    get_registered_backend_factory,
    instantiate_backend,
    list_registered_backend_factories,
    register_backend_factory,
    resolve_backend_factory,
)
from .interfaces import (
    CandidateListOptionsLike,
    KnowledgeBackend,
    KnowledgeBackendError,
    KNOWLEDGE_BACKEND_VERSION,
    ListQueryOptionsLike,
    MemoryBundleExportOptionsLike,
    MemoryBundleImportOptionsLike,
    MemoryBundleImportResultLike,
    MemoryBundleSnapshotLike,
    MemoryCandidateLike,
    MemoryRecordLike,
    MemoryRelationLike,
    MemoryTierTransitionLike,
    MemoryTypeLike,
    SearchQueryOptionsLike,
    ensure_backend_compatibility,
)

__all__ = [
    "CandidateListOptionsLike",
    "BackendMemoryStoreAdapter",
    "BuiltinKnowledgeBackend",
    "DEFAULT_SOPHIAGRAPH_BACKEND_PROVIDER",
    "KnowledgeBackend",
    "KnowledgeBackendConfig",
    "KnowledgeBackendError",
    "KnowledgeBackendFactory",
    "KNOWLEDGE_BACKEND_VERSION",
    "ListQueryOptionsLike",
    "MemoryBundleExportOptionsLike",
    "MemoryBundleImportOptionsLike",
    "MemoryBundleImportResultLike",
    "MemoryBundleSnapshotLike",
    "MemoryCandidateLike",
    "MemoryRecordLike",
    "MemoryRelationLike",
    "MemoryTierTransitionLike",
    "MemoryTypeLike",
    "NoneKnowledgeBackend",
    "ResolvedKnowledgeBackendFactory",
    "SearchQueryOptionsLike",
    "adapt_backend_to_store",
    "ensure_backend_compatibility",
    "get_registered_backend_factory",
    "instantiate_backend",
    "list_registered_backend_factories",
    "register_backend_factory",
    "resolve_backend_config",
    "resolve_backend_factory",
]
