from openminion.cli.bootstrap.loader import load_config
from openminion.services.diagnostics.debug import DebugStatus, WiringSource

from . import cli as _cli
from .registry import register_core_providers as _register_core_providers
from .providers.core import (
    OpenMinionDebugProvider,
    OpenMinionToolsDebugProvider,
    OpenMinionPluginsDebugProvider,
)
from .providers.modules import (
    OpenMinionRetrieveDebugProvider,
    OpenMinionSessionDebugProvider,
    OpenMinionContextDebugProvider,
    OpenMinionMemoryDebugProvider,
    OpenMinionCompressDebugProvider,
    OpenMinionSkillDebugProvider,
    OpenMinionRegistryDebugProvider,
    OpenMinionTelemetryDebugProvider,
    OpenMinionControlplaneDebugProvider,
    OpenMinionIdentityDebugProvider,
)
from .providers.tools import (
    OpenMinionWeatherDebugProvider,
    OpenMinionTavilyDebugProvider,
    OpenMinionReactionsDebugProvider,
)


def run_debug(args) -> int:
    _cli.load_config = load_config
    return _cli.run_debug(args)


__all__ = [
    "load_config",
    "run_debug",
    "_register_core_providers",
    "OpenMinionDebugProvider",
    "OpenMinionToolsDebugProvider",
    "OpenMinionPluginsDebugProvider",
    "OpenMinionRetrieveDebugProvider",
    "OpenMinionSessionDebugProvider",
    "OpenMinionContextDebugProvider",
    "OpenMinionMemoryDebugProvider",
    "OpenMinionCompressDebugProvider",
    "OpenMinionSkillDebugProvider",
    "OpenMinionRegistryDebugProvider",
    "OpenMinionTelemetryDebugProvider",
    "OpenMinionControlplaneDebugProvider",
    "OpenMinionIdentityDebugProvider",
    "OpenMinionWeatherDebugProvider",
    "OpenMinionTavilyDebugProvider",
    "OpenMinionReactionsDebugProvider",
    "DebugStatus",
    "WiringSource",
]
