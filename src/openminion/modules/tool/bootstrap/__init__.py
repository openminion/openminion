from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.manager import ToolRegistryManager

from . import entries as _entries
from . import registration as _registration
from . import runtime_build as _runtime_build

_ToolBootstrapEntry = _entries._ToolBootstrapEntry
_ToolBootstrapRecord = _entries._ToolBootstrapRecord
_MCP_TOOL_BOOTSTRAP_ENTRY = _entries._MCP_TOOL_BOOTSTRAP_ENTRY
_entry_enabled = _entries._entry_enabled
_entry_enabled_for_runtime_config = _entries._entry_enabled_for_runtime_config
_dynamic_tool_bootstrap_entries = _entries._dynamic_tool_bootstrap_entries
_prepare_tool_register_state = _entries._prepare_tool_register_state
_apply_dynamic_runtime_ownership = _entries._apply_dynamic_runtime_ownership
_prepared_state_record_details = _entries._prepared_state_record_details
_TOOL_BOOTSTRAP_ENTRIES = _entries._TOOL_BOOTSTRAP_ENTRIES

_ManifestCandidateValidationError = _registration._ManifestCandidateValidationError
_require_registrar_protocol = _registration._require_registrar_protocol
_resolve_module_registrar = _registration._resolve_module_registrar
_validate_manifest_runtime_candidates = (
    _registration._validate_manifest_runtime_candidates
)
_is_empty_provider_only_manifest = _registration._is_empty_provider_only_manifest
_validate_manifest_contract = _registration._validate_manifest_contract
_register_module_plugin = _registration._register_module_plugin
_register_provider_plugin = _registration._register_provider_plugin

RuntimeBootstrap = _runtime_build.RuntimeBootstrap
_collect_runtime_tool_schemas = _runtime_build._collect_runtime_tool_schemas
_ci_mode_enabled = _runtime_build._ci_mode_enabled
_emit_contract_drift_report = _runtime_build._emit_contract_drift_report


def _bootstrap_default_registry(
    registry: ToolRegistry,
    registry_manager: ToolRegistryManager,
    *,
    modules_only: bool = False,
) -> list[_ToolBootstrapRecord]:
    return _registration._bootstrap_default_registry(
        registry,
        registry_manager,
        modules_only=modules_only,
        tool_bootstrap_entries=_TOOL_BOOTSTRAP_ENTRIES,
    )


def build_runtime_bootstrap(
    *,
    config: object | None = None,
    workspace_root: object | None = None,
    run_root: object | None = None,
    strict: bool = True,
) -> RuntimeBootstrap:
    return _runtime_build.build_runtime_bootstrap(
        config=config,
        workspace_root=workspace_root,
        run_root=run_root,
        strict=strict,
        tool_bootstrap_entries=_TOOL_BOOTSTRAP_ENTRIES,
    )


def wire_default_tool_registry_manager() -> None:
    _runtime_build.wire_default_tool_registry_manager(
        tool_bootstrap_entries=_TOOL_BOOTSTRAP_ENTRIES,
    )


__all__ = [
    "RuntimeBootstrap",
    "build_runtime_bootstrap",
    "wire_default_tool_registry_manager",
    "_ToolBootstrapEntry",
    "_ToolBootstrapRecord",
    "_TOOL_BOOTSTRAP_ENTRIES",
]
