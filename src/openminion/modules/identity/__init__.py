from openminion.base.version import OPENMINION_VERSION

from .interfaces import (
    IDENTITY_INTERFACE_VERSION,
    IdentityCtlInterface,
    ensure_identity_compatibility,
)
from .models import AgentProfile, IdentitySnippet
from .runtime.bundle import IdentityBundle, IdentityDocument, load_identity_bundle
from .runtime.operator import (
    IdentityOperatorError,
    apply_identity_candidate,
    inspect_identity,
    reload_runtime_identity,
    runtime_identity_snapshot,
    validate_identity_candidate,
    verify_runtime_identity,
)
from .runtime.service import IdentityCtl
from .storage import InMemoryIdentityStore, SQLiteIdentityStore

__all__ = [
    "AgentProfile",
    "IdentityCtl",
    "IdentityCtlInterface",
    "IdentityBundle",
    "IdentityDocument",
    "IdentityOperatorError",
    "IdentitySnippet",
    "InMemoryIdentityStore",
    "SQLiteIdentityStore",
    "IDENTITY_INTERFACE_VERSION",
    "ensure_identity_compatibility",
    "apply_identity_candidate",
    "inspect_identity",
    "load_identity_bundle",
    "reload_runtime_identity",
    "runtime_identity_snapshot",
    "validate_identity_candidate",
    "verify_runtime_identity",
]

__version__ = OPENMINION_VERSION
