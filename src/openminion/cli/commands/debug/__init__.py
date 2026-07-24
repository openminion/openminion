from openminion.cli.config import load_cli_config_from_args
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


load_config = load_cli_config_from_args


def run_debug(args) -> int:
    def _load_for_cli(config_path):
        if load_config is load_cli_config_from_args:
            return load_config(args)
        return load_config(config_path)

    _cli.load_config = _load_for_cli
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
