from typing import TYPE_CHECKING

from .registrar import REGISTRAR as _REGISTRAR
from .models import (
    BrowserCallArgs,
    BrowserCapabilities,
    BrowserOp,
    BrowserResult,
    SUPPORTED_OPS,
    normalize_op,
)
from .providers import BrowserProvider, BrowserProviderContext, BrowserProviderRegistry
from .router import BrowserRouter, BrowserRoutingConfig
from .tool import (
    BROWSER_TOOL_INPUT_SCHEMA,
    default_browser_tool,
    provider_registry,
    register,
    register_provider,
)

if TYPE_CHECKING:
    from openminion.modules.tool.runtime.registrar import ToolModuleRegistrar

REGISTRAR: "ToolModuleRegistrar" = _REGISTRAR

__all__ = [
    "REGISTRAR",
    "BROWSER_TOOL_INPUT_SCHEMA",
    "BrowserCallArgs",
    "BrowserCapabilities",
    "BrowserOp",
    "BrowserProvider",
    "BrowserProviderContext",
    "BrowserProviderRegistry",
    "BrowserResult",
    "BrowserRouter",
    "BrowserRoutingConfig",
    "SUPPORTED_OPS",
    "default_browser_tool",
    "normalize_op",
    "provider_registry",
    "register",
    "register_provider",
]
