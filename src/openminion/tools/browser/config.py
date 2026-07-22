from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openminion.tools.config import ToolEnv, resolve_tool_env

from .constants import OPENMINION_BROWSER_DEFAULT_PROVIDER_ENV


@dataclass(frozen=True)
class BrowserToolConfig:
    default_provider: str = ""


def load_config(*, env: ToolEnv | Mapping[str, Any] | None = None) -> BrowserToolConfig:
    resolved = resolve_tool_env(env=env)
    return BrowserToolConfig(
        default_provider=str(
            resolved.get(OPENMINION_BROWSER_DEFAULT_PROVIDER_ENV, "")
        ).strip()
    )


__all__ = ["BrowserToolConfig", "load_config"]
