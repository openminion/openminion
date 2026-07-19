from openminion.base.version import OPENMINION_VERSION

from .interfaces import (
    SESSION_INTERFACE_VERSION,
    SessionContextClientAPI,
    SessionStoreAPI,
    ensure_session_component_compatibility,
)
from .runtime.factory import build_module_session_store
from .runtime.continuation import SessionContinuationService
from .storage.store import PostgresSessionStore, SQLiteSessionStore, SliceLimits

__all__ = [
    "SESSION_INTERFACE_VERSION",
    "SessionContextClientAPI",
    "SessionStoreAPI",
    "SessionContinuationService",
    "PostgresSessionStore",
    "SQLiteSessionStore",
    "SliceLimits",
    "build_module_session_store",
    "ensure_session_component_compatibility",
    "__version__",
]

__version__ = OPENMINION_VERSION
