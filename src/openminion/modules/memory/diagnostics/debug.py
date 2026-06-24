from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.base.config.env import resolve_environment_config
from openminion.modules.memory.config import MemctlConfig, load_config


@dataclass
class MemoryDebugInfo:
    """Memory debug information."""

    config_path: str | None
    sqlite_path: str | None
    path_mode: str
    path_source: str
    version: int
    backend: str
    home_root: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": {
                "config_path": self.config_path,
                "sqlite_path": self.sqlite_path,
                "resolved_paths": {
                    "config": self.config_path,
                    "storage": self.sqlite_path,
                },
                "path_mode": self.path_mode,
                "path_source": self.path_source,
            },
            "config": {
                "version": self.version,
                "backend": self.backend,
            },
            "runtime": {
                "home_root": self.home_root,
            },
        }


def _fallback_debug_info(
    config_path: str | None,
    home_root: Path | None,
) -> MemoryDebugInfo:
    return MemoryDebugInfo(
        config_path=config_path,
        sqlite_path=None,
        path_mode="module_standalone",
        path_source="default_standalone",
        version=1,
        backend="unknown",
        home_root=str(home_root) if home_root else None,
    )


def _resolution_step(step: int, source: str, **payload: Any) -> dict[str, Any]:
    return {"step": step, "source": source, **payload}


class MemoryDebugProvider:
    """Debug provider for memory path and config diagnostics."""

    def __init__(self, config: MemctlConfig | None = None):
        self._config = config

    def get_debug_info(
        self,
        *,
        config_path: str | None = None,
        home_root: Path | None = None,
    ) -> MemoryDebugInfo:
        cfg = self._config
        if cfg is None:
            try:
                cfg = load_config(
                    path=config_path,
                    home_root=home_root,
                )
            except Exception:
                return _fallback_debug_info(config_path, home_root)

        return MemoryDebugInfo(
            config_path=config_path,
            sqlite_path=str(cfg.store.sqlite_path) if cfg.store.sqlite_path else None,
            path_mode=cfg.path_mode,
            path_source=cfg.path_source,
            version=cfg.version,
            backend=cfg.store.backend,
            home_root=str(home_root) if home_root else None,
        )

    def get_path_diagnostics(self) -> dict[str, Any]:
        env = resolve_environment_config()
        home_root_env = str(env.get("OPENMINION_HOME", "") or "").strip()
        standalone = str(env.get("OPENMINION_MODULE_STANDALONE", "") or "").strip()
        is_standalone = standalone.lower() in {"1", "true", "yes"}
        return {
            "environment": {
                "OPENMINION_HOME": env.get("OPENMINION_HOME", "not set"),
                "MEMCTL_CONFIG": env.get("MEMCTL_CONFIG", "not set"),
                "OPENMINION_MODULE_STANDALONE": env.get(
                    "OPENMINION_MODULE_STANDALONE", "not set"
                ),
            },
            "resolution_chain": [
                _resolution_step(
                    1,
                    "OPENMINION_HOME env var",
                    **(
                        {
                            "home_root": home_root_env,
                            "mode": "integrated_runtime",
                        }
                        if home_root_env
                        else {"status": "not set"}
                    ),
                ),
                _resolution_step(
                    2,
                    "OPENMINION_MODULE_STANDALONE",
                    **(
                        {"mode": "module_standalone"}
                        if is_standalone
                        else {"status": "not set or false"}
                    ),
                ),
                _resolution_step(
                    3,
                    "default fallback",
                    mode="integrated_runtime" if home_root_env else "module_standalone",
                ),
            ],
        }


def get_memory_debug_provider() -> MemoryDebugProvider:
    return MemoryDebugProvider()
