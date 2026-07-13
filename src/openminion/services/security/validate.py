"""Service composition wrapper for module-owned security diagnostics."""

from collections.abc import Sequence
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.policy.diagnostics.security import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARN,
    SecurityValidateFinding,
    SecurityValidateReport,
    run_security_validate as _run_security_validate,
)
from openminion.services.agent.memory import resolve_memory_root
from openminion.services.runtime.plugins import PluginManifest


def run_security_validate(
    *,
    config: OpenMinionConfig,
    config_path: Path,
    storage_path: Path,
    loaded_plugin_manifest_ids: Sequence[str] | None = None,
    loaded_plugin_manifests: Sequence[PluginManifest] | None = None,
    loaded_tool_names: Sequence[str] | None = None,
) -> SecurityValidateReport:
    memory_root = resolve_memory_root(
        config=config,
        config_path=config_path,
        storage_path=storage_path,
    )
    return _run_security_validate(
        config=config,
        config_path=config_path,
        storage_path=storage_path,
        memory_root=memory_root,
        loaded_plugin_manifest_ids=loaded_plugin_manifest_ids,
        loaded_plugin_manifests=loaded_plugin_manifests,
        loaded_tool_names=loaded_tool_names,
    )


__all__ = [
    "SEVERITY_CRITICAL",
    "SEVERITY_INFO",
    "SEVERITY_WARN",
    "SecurityValidateFinding",
    "SecurityValidateReport",
    "run_security_validate",
]
