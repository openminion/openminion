from typing import TYPE_CHECKING, Any

from .contracts import (
    CONTEXT_CLIENT_INTERFACE_VERSION,
    ensure_context_client_compatibility,
)

if TYPE_CHECKING:  # pragma: no cover - import-time contract only
    from .builder import ContextPackBuilder
    from .prefix import PinnedPrefixBuilder
    from .service import ContextCtlService, IdentityMissingError

__all__ = [
    "CONTEXT_CLIENT_INTERFACE_VERSION",
    "ContextCtlService",
    "ContextPackBuilder",
    "IdentityMissingError",
    "PinnedPrefixBuilder",
    "ensure_context_client_compatibility",
]


def __getattr__(name: str) -> Any:
    if name == "ContextPackBuilder":
        from .builder import ContextPackBuilder

        return ContextPackBuilder
    if name == "PinnedPrefixBuilder":
        from .prefix import PinnedPrefixBuilder

        return PinnedPrefixBuilder
    if name == "ContextCtlService":
        from .service import ContextCtlService

        return ContextCtlService
    if name == "IdentityMissingError":
        from .service import IdentityMissingError

        return IdentityMissingError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
