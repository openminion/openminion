"""Configuration for Daytona-backed runtime sandboxing."""

from dataclasses import dataclass
import os
from typing import Mapping

from openminion.modules.runtime.constants import (
    DAYTONA_DEFAULT_API_KEY_ENV,
    DAYTONA_DEFAULT_WORKSPACE_IMAGE,
)


@dataclass(frozen=True)
class DaytonaConfig:
    """Operator-owned configuration for the Daytona sandbox client."""

    endpoint: str
    api_key: str = ""
    api_key_env_var: str = DAYTONA_DEFAULT_API_KEY_ENV
    default_workspace_image: str = DAYTONA_DEFAULT_WORKSPACE_IMAGE
    connect_timeout_s: float = 5.0
    command_timeout_s: float = 30.0
    max_output_bytes: int = 1_048_576
    verify_tls: bool = True

    def __post_init__(self) -> None:
        endpoint = str(self.endpoint or "").strip()
        if not endpoint:
            raise ValueError("DaytonaConfig.endpoint is required")
        if float(self.connect_timeout_s) <= 0:
            raise ValueError("DaytonaConfig.connect_timeout_s must be > 0")
        if float(self.command_timeout_s) <= 0:
            raise ValueError("DaytonaConfig.command_timeout_s must be > 0")
        if int(self.max_output_bytes) <= 0:
            raise ValueError("DaytonaConfig.max_output_bytes must be > 0")

    def resolve_api_key(self, env: Mapping[str, str] | None = None) -> str:
        if str(self.api_key or "").strip():
            return str(self.api_key).strip()
        source = env or os.environ
        return str(source.get(self.api_key_env_var, "") or "").strip()

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None,
    ) -> "DaytonaConfig | None":
        source = env or {}
        endpoint = str(source.get("OPENMINION_DAYTONA_ENDPOINT", "") or "").strip()
        if not endpoint:
            return None
        connect_timeout = float(
            str(source.get("OPENMINION_DAYTONA_CONNECT_TIMEOUT_S", "") or "").strip()
            or 5.0
        )
        command_timeout = float(
            str(source.get("OPENMINION_DAYTONA_COMMAND_TIMEOUT_S", "") or "").strip()
            or 30.0
        )
        max_output_bytes = int(
            str(source.get("OPENMINION_DAYTONA_MAX_OUTPUT_BYTES", "") or "").strip()
            or 1_048_576
        )
        verify_tls_raw = str(
            source.get("OPENMINION_DAYTONA_VERIFY_TLS", "") or ""
        ).strip()
        verify_tls = verify_tls_raw.lower() not in {"0", "false", "no", "off"}
        return cls(
            endpoint=endpoint,
            api_key=str(source.get("OPENMINION_DAYTONA_API_KEY", "") or "").strip(),
            api_key_env_var=str(
                source.get("OPENMINION_DAYTONA_API_KEY_ENV", "")
                or DAYTONA_DEFAULT_API_KEY_ENV
            ).strip(),
            default_workspace_image=str(
                source.get("OPENMINION_DAYTONA_IMAGE", "")
                or DAYTONA_DEFAULT_WORKSPACE_IMAGE
            ).strip(),
            connect_timeout_s=connect_timeout,
            command_timeout_s=command_timeout,
            max_output_bytes=max_output_bytes,
            verify_tls=verify_tls,
        )


__all__ = ["DaytonaConfig"]
