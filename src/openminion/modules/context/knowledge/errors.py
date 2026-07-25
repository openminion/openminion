"""Typed errors for the knowledge-graph layer."""

from typing import Any, ClassVar


class KnowledgeGraphError(RuntimeError):
    """Base error carrying a stable code and optional details."""

    code: ClassVar[str] = "KNOWLEDGE_GRAPH_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


class InvalidLayerError(KnowledgeGraphError):
    code = "INVALID_LAYER"


class InvalidProviderTagError(KnowledgeGraphError):
    code = "INVALID_PROVIDER_TAG"


class InvalidCapabilityError(KnowledgeGraphError):
    code = "INVALID_CAPABILITY"


class UnsupportedCapabilityError(KnowledgeGraphError):
    code = "UNSUPPORTED_CAPABILITY"


class UnknownProviderError(KnowledgeGraphError):
    code = "UNKNOWN_PROVIDER"


class DuplicateProviderError(KnowledgeGraphError):
    code = "DUPLICATE_PROVIDER"


class MissingRequiredCapabilityError(KnowledgeGraphError):
    code = "MISSING_REQUIRED_CAPABILITY"


class DisabledProviderError(KnowledgeGraphError):
    code = "DISABLED_PROVIDER"


class MultiActiveSecondBrainError(KnowledgeGraphError):
    code = "MULTI_ACTIVE_SECOND_BRAIN_REJECTED"


class HybridDurableMemoryError(KnowledgeGraphError):
    """Hybrids must delegate durable writes; they cannot advertise durable_memory."""

    code = "HYBRID_DURABLE_MEMORY_REJECTED"


class GraphViewerUnavailableError(KnowledgeGraphError):
    code = "GRAPH_VIEWER_UNAVAILABLE"


class GraphViewerSourceError(KnowledgeGraphError):
    code = "GRAPH_VIEWER_SOURCE_ERROR"
