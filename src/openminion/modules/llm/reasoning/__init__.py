from openminion.base.version import OPENMINION_VERSION

from .interfaces import (
    THINKING_INTERFACE_VERSION,
    ThinkingCtlInterface,
    ensure_thinking_compatibility,
)
from .mapping import normalize_optional_reasoning_profile
from .constants import (
    THINKING_ROUTE_BRIDGE,
    THINKING_ROUTE_DIRECT,
    THINKING_ROUTE_OLLAMA,
)
from .resolver import (
    build_runtime_thinking_diagnostics,
    resolve_mode_aware_thinking,
    resolve_thinking,
)
from .schemas import (
    ModeThinkingPolicy,
    ThinkingRequest,
    ThinkingResolved,
    ThinkingResolutionInput,
    ThinkingRuntimeDiagnostics,
)
from .service import ThinkingCtl

__version__ = OPENMINION_VERSION

__all__ = [
    "THINKING_INTERFACE_VERSION",
    "THINKING_ROUTE_BRIDGE",
    "THINKING_ROUTE_DIRECT",
    "THINKING_ROUTE_OLLAMA",
    "ModeThinkingPolicy",
    "ThinkingCtl",
    "ThinkingCtlInterface",
    "ThinkingRequest",
    "ThinkingResolved",
    "ThinkingResolutionInput",
    "ThinkingRuntimeDiagnostics",
    "build_runtime_thinking_diagnostics",
    "ensure_thinking_compatibility",
    "normalize_optional_reasoning_profile",
    "resolve_mode_aware_thinking",
    "resolve_thinking",
    "__version__",
]
