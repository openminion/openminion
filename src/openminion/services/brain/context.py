from dataclasses import dataclass
from typing import Any

from openminion.base.config import ConfigManager, HomePaths


@dataclass(frozen=True)
class BrainBridgeContext:
    home_paths: HomePaths
    workspace_root: str
    config_manager: ConfigManager | None
    telemetryctl: Any | None
    mode: str
