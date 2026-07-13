from openminion.base.version import OPENMINION_VERSION

from .interfaces import (
    IDENTITY_INTERFACE_VERSION,
    IdentityCtlInterface,
    ensure_identity_compatibility,
)
from .models import AgentProfile, IdentitySnippet
from .runtime.bundle import IdentityBundle, IdentityDocument, load_identity_bundle
from .runtime.service import IdentityCtl
from .storage import InMemoryIdentityStore, SQLiteIdentityStore

__all__ = [
    "AgentProfile",
    "IdentityCtl",
    "IdentityCtlInterface",
    "IdentityBundle",
    "IdentityDocument",
    "IdentitySnippet",
    "InMemoryIdentityStore",
    "SQLiteIdentityStore",
    "IDENTITY_INTERFACE_VERSION",
    "ensure_identity_compatibility",
    "load_identity_bundle",
]

__version__ = OPENMINION_VERSION
