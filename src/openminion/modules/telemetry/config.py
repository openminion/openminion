from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openminion.base.config import (
    OTELExporterConfig,
    OpenMinionConfig,
    RuntimeConfig,
)
from openminion.base.config.io import load_config as load_base_config


@dataclass(frozen=True)
class TelemetryConfig:
    otel_exporter: OTELExporterConfig = field(default_factory=OTELExporterConfig)


def load_config(
    payload: str
    | Path
    | dict[str, Any]
    | OpenMinionConfig
    | RuntimeConfig
    | None = None,
    *,
    home_root: str | Path | None = None,
) -> TelemetryConfig:
    runtime_config = _coerce_runtime_config(payload, home_root=home_root)
    return TelemetryConfig(otel_exporter=runtime_config.telemetry_exporter)


def _coerce_runtime_config(
    payload: str | Path | dict[str, Any] | OpenMinionConfig | RuntimeConfig | None,
    *,
    home_root: str | Path | None,
) -> RuntimeConfig:
    resolved_home_root = Path(home_root).expanduser() if home_root is not None else None
    if payload is None:
        return load_base_config(home_root=resolved_home_root).runtime
    if isinstance(payload, RuntimeConfig):
        return payload
    if isinstance(payload, OpenMinionConfig):
        return payload.runtime
    if isinstance(payload, dict):
        return OpenMinionConfig.from_dict(payload).runtime
    config_path = str(Path(payload).expanduser())
    return load_base_config(
        config_path=config_path, home_root=resolved_home_root
    ).runtime
