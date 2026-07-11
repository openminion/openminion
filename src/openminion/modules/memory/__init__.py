from openminion.base.version import OPENMINION_VERSION

from .interfaces import (
    MEMORY_INTERFACE_VERSION,
    ListQueryOptions,
    MemoryNamespaceQueryInterface,
    MemoryServiceInterface,
    SearchQueryOptions,
    ensure_memory_compatibility,
)
from .runtime.consolidation.coordinator import MAINTENANCE_MODULE_STATE_KEY
from .runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)
from .runtime.scope import resolve_namespace_filter
from .service import MemoryService

__all__ = [
    "default_provenance_recorder",
    "MAINTENANCE_MODULE_STATE_KEY",
    "MemoryService",
    "MemoryServiceInterface",
    "MemoryNamespaceQueryInterface",
    "MemoryProvenanceRecorder",
    "ListQueryOptions",
    "MEMORY_INTERFACE_VERSION",
    "SearchQueryOptions",
    "resolve_namespace_filter",
    "set_default_provenance_recorder",
    "ensure_memory_compatibility",
    "__version__",
]

__version__ = OPENMINION_VERSION
