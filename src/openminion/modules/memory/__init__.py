from .interfaces import (
    MEMORY_INTERFACE_VERSION,
    MemoryServiceInterface,
    ensure_memory_compatibility,
)
from .runtime.consolidation.coordinator import MAINTENANCE_MODULE_STATE_KEY
from .runtime.provenance import (
    MemoryProvenanceRecorder,
    default_provenance_recorder,
    set_default_provenance_recorder,
)
from .service import MemoryService

__all__ = [
    "default_provenance_recorder",
    "MAINTENANCE_MODULE_STATE_KEY",
    "MemoryService",
    "MemoryServiceInterface",
    "MemoryProvenanceRecorder",
    "MEMORY_INTERFACE_VERSION",
    "set_default_provenance_recorder",
    "ensure_memory_compatibility",
    "__version__",
]

__version__ = "0.0.1"
