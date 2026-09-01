from openminion.base.version import OPENMINION_VERSION

from .interfaces import (
    MEMORY_INTERFACE_VERSION,
    ListQueryOptions,
    MemoryNamespaceQueryInterface,
    MemoryServiceInterface,
    RecordOrder,
    SearchQueryOptions,
    ensure_memory_compatibility,
)
from .runtime.consolidation.coordinator import MAINTENANCE_MODULE_STATE_KEY
from .runtime.assembly import RuntimeMemoryAssembly, RuntimeMemoryScheduler
from .runtime import configuration as memory_runtime_configuration
from .runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)
from .runtime.recall import SophiagraphRecallAdapter
from .runtime.scope import resolve_namespace_filter
from .service import MemoryService

__all__ = [
    "default_provenance_recorder",
    "MAINTENANCE_MODULE_STATE_KEY",
    "MemoryService",
    "RuntimeMemoryAssembly",
    "RuntimeMemoryScheduler",
    "memory_runtime_configuration",
    "MemoryServiceInterface",
    "MemoryNamespaceQueryInterface",
    "MemoryProvenanceRecorder",
    "ListQueryOptions",
    "RecordOrder",
    "MEMORY_INTERFACE_VERSION",
    "SearchQueryOptions",
    "SophiagraphRecallAdapter",
    "resolve_namespace_filter",
    "set_default_provenance_recorder",
    "ensure_memory_compatibility",
    "__version__",
]

__version__ = OPENMINION_VERSION
