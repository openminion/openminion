from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.base.runtime.constants import DEFAULT_CONFIG_FILENAME


@dataclass
class CronRunLogConfig:
    max_bytes: int = 2_000_000
    keep_lines: int = 2_000

    def __post_init__(self) -> None:
        self.max_bytes = max(1, int(self.max_bytes))
        self.keep_lines = max(1, int(self.keep_lines))

    def as_dict(self) -> dict:
        return {
            "max_bytes": self.max_bytes,
            "keep_lines": self.keep_lines,
        }


@dataclass
class CronConfig:
    enabled: bool = True
    tick_ms: int = 2_000
    max_concurrent_runs: int = 4
    lease_ttl_seconds: int = 60
    session_retention: str | bool = "24h"
    run_log: CronRunLogConfig = field(default_factory=CronRunLogConfig)

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled)
        self.tick_ms = max(100, int(self.tick_ms))
        self.max_concurrent_runs = max(1, int(self.max_concurrent_runs))
        self.lease_ttl_seconds = max(1, int(self.lease_ttl_seconds))
        if isinstance(self.session_retention, str):
            self.session_retention = self.session_retention.strip() or "24h"

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "tick_ms": self.tick_ms,
            "max_concurrent_runs": self.max_concurrent_runs,
            "lease_ttl_seconds": self.lease_ttl_seconds,
            "session_retention": self.session_retention,
            "run_log": self.run_log.as_dict(),
        }


@dataclass
class RuntimeConfig:
    max_agents_hot: int = 8
    max_global_concurrency: int = 8
    agent_ttl_seconds: int = 1800
    sweep_interval_seconds: int = 5
    cron: CronConfig = field(default_factory=CronConfig)

    def __post_init__(self) -> None:
        self.max_agents_hot = max(1, int(self.max_agents_hot))
        self.max_global_concurrency = max(1, int(self.max_global_concurrency))
        self.agent_ttl_seconds = max(1, int(self.agent_ttl_seconds))
        self.sweep_interval_seconds = max(1, int(self.sweep_interval_seconds))
        if not isinstance(self.cron, CronConfig):
            self.cron = CronConfig(**dict(self.cron))

    @classmethod
    def from_yaml(cls, path: str = DEFAULT_CONFIG_FILENAME) -> "RuntimeConfig":
        if not os.path.exists(path):
            return cls()

        try:
            import yaml

            with open(path, encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except ImportError:
            raw = _parse_simple_yaml(path)

        raw_mapping = _mapping(raw)
        section = _mapping(raw_mapping.get("runtimectl", raw_mapping))
        cron_raw = _mapping(section.get("cron"))
        run_log_raw = _mapping(cron_raw.get("run_log"))
        run_log = CronRunLogConfig(
            max_bytes=run_log_raw.get("max_bytes", 2_000_000),
            keep_lines=run_log_raw.get("keep_lines", 2_000),
        )
        cron = CronConfig(
            enabled=cron_raw.get("enabled", True),
            tick_ms=cron_raw.get("tick_ms", 2_000),
            max_concurrent_runs=cron_raw.get("max_concurrent_runs", 4),
            lease_ttl_seconds=cron_raw.get("lease_ttl_seconds", 60),
            session_retention=cron_raw.get("session_retention", "24h"),
            run_log=run_log,
        )

        return cls(
            max_agents_hot=section.get("max_agents_hot", 8),
            max_global_concurrency=section.get("max_global_concurrency", 8),
            agent_ttl_seconds=section.get("agent_ttl_seconds", 1800),
            sweep_interval_seconds=section.get("sweep_interval_seconds", 5),
            cron=cron,
        )

    def as_dict(self) -> dict:
        return {
            "max_agents_hot": self.max_agents_hot,
            "max_global_concurrency": self.max_global_concurrency,
            "agent_ttl_seconds": self.agent_ttl_seconds,
            "sweep_interval_seconds": self.sweep_interval_seconds,
            "cron": self.cron.as_dict(),
        }


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> RuntimeConfig:
    _ = base_config, data_root
    runtime_path = (home_root / DEFAULT_CONFIG_FILENAME).resolve(strict=False)
    return RuntimeConfig.from_yaml(str(runtime_path))


def _mapping(value: object) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _parse_simple_yaml(path: str) -> dict:
    result: dict = {}
    current_section: dict | None = None

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.rstrip()
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.startswith(" ") and not stripped.startswith("\t"):
                if ":" in stripped:
                    k, _, v = stripped.partition(":")
                    k = k.strip()
                    v = v.strip()
                    if v == "":
                        current_section = {}
                        result[k] = current_section
                    else:
                        result[k] = _coerce(v)
                        current_section = None
            else:
                if current_section is not None and ":" in stripped:
                    k, _, v = stripped.strip().partition(":")
                    current_section[k.strip()] = _coerce(v.strip())

    return result


def _coerce(value: str) -> object:
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("null", "~", "None", ""):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


ManagerConfig = RuntimeConfig
