"""External durable-memory backend registration and capability validation."""

from .registry import (
    ExternalBackendCapabilityReport,
    ExternalBackendCapabilities,
    ExternalBackendRegistration,
    get_registered_external_backend,
    list_registered_external_backends,
    register_external_backend,
    resolve_external_backend,
    validate_external_backend,
)
from .reference_sqlite import (
    REFERENCE_SQLITE_ADAPTER_NAME,
    build_reference_sqlite_backend,
    register_reference_sqlite_backend,
)

__all__ = [
    "ExternalBackendCapabilityReport",
    "ExternalBackendCapabilities",
    "ExternalBackendRegistration",
    "get_registered_external_backend",
    "list_registered_external_backends",
    "REFERENCE_SQLITE_ADAPTER_NAME",
    "build_reference_sqlite_backend",
    "register_external_backend",
    "register_reference_sqlite_backend",
    "resolve_external_backend",
    "validate_external_backend",
]
