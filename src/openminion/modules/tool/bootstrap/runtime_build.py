import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Mapping

from openminion.base.config.env import resolve_environment_config
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.constants import (
    TOOL_BOOTSTRAP_GATE_ALWAYS,
    TOOL_BOOTSTRAP_STATUS_IMPORT_ERROR,
    TOOL_BOOTSTRAP_STATUS_REGISTRAR_FAILED,
    TOOL_BOOTSTRAP_STATUS_REGISTERED,
    TOOL_BOOTSTRAP_STATUS_SKIPPED_GATE,
)
from openminion.modules.tool.runtime.dispatch import set_registry, set_registry_manager
from openminion.modules.tool.runtime.manager import (
    ToolContractDriftReport,
    ToolRegistryManager,
)
from openminion.modules.tool.runtime.policy import ToolBindingPolicyManager
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.registry import ToolRegistry
from .entries import (
    _TOOL_BOOTSTRAP_ENTRIES,
    _ToolBootstrapEntry,
    _ToolBootstrapRecord,
    _apply_dynamic_runtime_ownership,
    _dynamic_tool_bootstrap_entries,
    _entry_enabled,
    _entry_enabled_for_runtime_config,
    _prepare_tool_register_state,
    _prepared_state_record_details,
)
from .registration import (
    _ManifestCandidateValidationError,
    _register_provider_plugin,
    _require_registrar_protocol,
    _resolve_module_registrar,
    _validate_manifest_contract,
    _validate_manifest_runtime_candidates,
)

logger = logging.getLogger("openminion.modules.tool.bootstrap")


def _collect_runtime_tool_schemas(registry: ToolRegistry) -> dict[str, dict[str, Any]]:
    schema_map: dict[str, dict[str, Any]] = {}
    for spec in registry.provider_specs():
        tool_name = str(getattr(spec, "name", "") or "").strip()
        if not tool_name:
            continue
        parameters = getattr(spec, "parameters", {}) or {}
        if isinstance(parameters, Mapping):
            schema_map[tool_name] = dict(parameters)
            continue
        schema_map[tool_name] = {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
    return schema_map


@dataclass
class RuntimeBootstrap:
    """Bootstrap object containing all runtime components."""

    registry: ToolRegistry
    policy_manager: ToolBindingPolicyManager
    manager: ToolRegistryManager
    mcp_manager: Any | None = None
    contract_drift_report: ToolContractDriftReport | None = None
    config: Any | None = None
    bootstrap_records: list[_ToolBootstrapRecord] | None = None


def _ci_mode_enabled() -> bool:
    token = resolve_environment_config().get("CI", "").strip().lower()
    return token in {"1", "true", "yes", "on"}


def _emit_contract_drift_report(
    registry_manager: ToolRegistryManager,
) -> ToolContractDriftReport:
    report = registry_manager.contract_drift_report()
    if report.has_drift:
        payload = {
            "model_tool_ids_missing_from_manifests": list(
                report.model_tool_ids_missing_from_manifests
            ),
            "model_tool_ids_missing_from_contracts": list(
                report.model_tool_ids_missing_from_contracts
            ),
            "runtime_binding_ids_missing_from_manifests": list(
                report.runtime_binding_ids_missing_from_manifests
            ),
            "runtime_binding_ids_missing_from_contracts": list(
                report.runtime_binding_ids_missing_from_contracts
            ),
        }
        logger.error("tool.contract_drift=%s", payload)
        if _ci_mode_enabled():
            raise ToolRuntimeError(
                "INTERNAL_ERROR",
                f"Tool contract drift detected: {payload}",
                {"payload": payload}
                if isinstance(payload, dict)
                else {"payload": str(payload)},
            )
    else:
        logger.info("tool.contract_drift=clean")
    return report


def build_runtime_bootstrap(
    *,
    config: Any | None = None,
    workspace_root: Any | None = None,
    run_root: Any | None = None,
    strict: bool = True,
    tool_bootstrap_entries: tuple[_ToolBootstrapEntry, ...] | None = None,
) -> RuntimeBootstrap:
    """Build runtime bootstrap with module manifests and policy from config."""
    from openminion.base.config.tool_selection.parser import (
        _DEFAULT_RUNTIME_FALLBACK_ON,
        _DEFAULT_RUNTIME_NO_FALLBACK_ON,
    )

    registry_manager = ToolRegistryManager()
    registry = ToolRegistry([])

    workspace_path = Path(workspace_root) if workspace_root else None
    run_path = Path(run_root) if run_root else None

    bootstrap_records: list[_ToolBootstrapRecord] = []

    for entry in _dynamic_tool_bootstrap_entries(
        config,
        tool_bootstrap_entries=tool_bootstrap_entries or _TOOL_BOOTSTRAP_ENTRIES,
    ):
        enabled = _entry_enabled(entry) and _entry_enabled_for_runtime_config(
            entry, config
        )
        if not enabled:
            bootstrap_records.append(
                _ToolBootstrapRecord(
                    kind=entry.kind,
                    module_name=entry.module_name,
                    label=entry.label,
                    required=entry.required,
                    gate=entry.gate,
                    enabled=False,
                    status=TOOL_BOOTSTRAP_STATUS_SKIPPED_GATE,
                )
            )
            continue

        if entry.kind == "provider":
            record = _register_provider_plugin(
                module_name=entry.module_name, label=entry.label
            )
            bootstrap_records.append(record)
            continue

        prepared_state = _prepare_tool_register_state(entry=entry, config=config)
        ctx = ToolRegisterContext(
            module_id=entry.label.lower().replace(" ", "_"),
            config=config,
            workspace_root=workspace_path,
            run_root=run_path,
            prepared_state=prepared_state,
            strict=strict and entry.required,
        )

        try:
            module = importlib.import_module(entry.module_name)
        except ImportError as exc:
            if entry.required and strict:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Required module {entry.module_name} not found: {exc}",
                    {"module_name": entry.module_name},
                ) from exc
            logger.debug(
                "%s plugin unavailable (%s): %s", entry.label, entry.module_name, exc
            )
            bootstrap_records.append(
                _ToolBootstrapRecord(
                    kind=entry.kind,
                    module_name=entry.module_name,
                    label=entry.label,
                    required=entry.required,
                    gate=entry.gate,
                    enabled=True,
                    status=TOOL_BOOTSTRAP_STATUS_IMPORT_ERROR,
                    error=str(exc),
                )
            )
            continue

        registrar, registrar_module_name = _resolve_module_registrar(
            entry.module_name,
            module,
        )
        try:
            typed_registrar = _require_registrar_protocol(
                module_name=entry.module_name,
                label=entry.label,
                registrar=registrar,
            )
            manifest = typed_registrar.get_manifest(ctx)
            manifest_for_validation = _validate_manifest_contract(
                module_name=entry.module_name,
                label=entry.label,
                is_provider_only=typed_registrar.is_provider_only,
                manifest=manifest,
            )
            if manifest_for_validation is not None:
                registry_manager.register_module_manifest(
                    manifest_for_validation, source_module=entry.module_name
                )
            typed_registrar.register(registry, ctx)
            _apply_dynamic_runtime_ownership(
                registry=registry,
                prepared_state=prepared_state,
            )
            if manifest_for_validation is not None:
                _validate_manifest_runtime_candidates(
                    module_name=entry.module_name,
                    label=entry.label,
                    manifest=manifest_for_validation,
                    registry=registry,
                )
            added_runtime_tools, error_summary = _prepared_state_record_details(
                prepared_state
            )
            logger.info(
                "%s: registered via REGISTRAR (%s)",
                entry.label,
                registrar_module_name,
            )
            bootstrap_records.append(
                _ToolBootstrapRecord(
                    kind=entry.kind,
                    module_name=entry.module_name,
                    label=entry.label,
                    required=entry.required,
                    gate=entry.gate,
                    enabled=True,
                    status=TOOL_BOOTSTRAP_STATUS_REGISTERED,
                    error=error_summary,
                    added_runtime_tools=added_runtime_tools,
                )
            )
            continue
        except Exception as exc:
            if isinstance(exc, _ManifestCandidateValidationError):
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Module {entry.module_name} REGISTRAR failed: {exc}",
                    {"module_name": entry.module_name},
                ) from exc
            if isinstance(exc, TypeError):
                raise
            if entry.required and strict:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Module {entry.module_name} REGISTRAR failed: {exc}",
                    {"module_name": entry.module_name},
                ) from exc
            logger.warning(
                "%s REGISTRAR failed (%s): %s", entry.label, entry.module_name, exc
            )
            bootstrap_records.append(
                _ToolBootstrapRecord(
                    kind=entry.kind,
                    module_name=entry.module_name,
                    label=entry.label,
                    required=entry.required,
                    gate=entry.gate,
                    enabled=True,
                    status=TOOL_BOOTSTRAP_STATUS_REGISTRAR_FAILED,
                    error=str(exc),
                )
            )
            continue

    registry_manager.set_runtime_tool_schemas(_collect_runtime_tool_schemas(registry))
    registry_manager.compile()
    contract_drift_report = _emit_contract_drift_report(registry_manager)

    # Wire the populated manager into the resolver module
    set_registry_manager(registry_manager)
    set_registry(registry)

    if config is not None:
        tool_selection = getattr(config, "tool_selection", None)
        if tool_selection is not None:
            default_policies = {
                binding_id: policy
                for binding_id, (primary, fallback_tools) in (
                    registry_manager.runtime_binding_policy_defaults().items()
                )
                if (
                    policy := ToolBindingPolicyManager.default_policy(
                        binding_id,
                        (primary, *fallback_tools),
                    )
                )
                is not None
            }
            policy_manager = (
                ToolBindingPolicyManager.from_tool_selection_config_with_defaults(
                    tool_selection,
                    default_policies=default_policies,
                )
            )
        else:
            policy_manager = ToolBindingPolicyManager(
                fallback_on=_DEFAULT_RUNTIME_FALLBACK_ON,
                no_fallback_on=_DEFAULT_RUNTIME_NO_FALLBACK_ON,
            )
    else:
        policy_manager = ToolBindingPolicyManager(
            fallback_on=_DEFAULT_RUNTIME_FALLBACK_ON,
            no_fallback_on=_DEFAULT_RUNTIME_NO_FALLBACK_ON,
        )

    return RuntimeBootstrap(
        registry=registry,
        policy_manager=policy_manager,
        manager=registry_manager,
        mcp_manager=registry.mcp_manager,
        contract_drift_report=contract_drift_report,
        config=config,
        bootstrap_records=bootstrap_records,
    )


def _tool_bootstrap_entry_enabled(entry: _ToolBootstrapEntry) -> bool:
    gate = str(entry.gate or TOOL_BOOTSTRAP_GATE_ALWAYS).strip().lower()
    if gate == "never":
        return False
    if gate == TOOL_BOOTSTRAP_GATE_ALWAYS:
        return True
    env_val = (
        resolve_environment_config()
        .get(f"OPENMINION_TOOL_GATE_{gate.upper()}", "1")
        .strip()
    )
    return env_val in ("1", "true", "yes", "on")


def _validate_manifest_ids(
    *,
    entry: _ToolBootstrapEntry,
    model_tools: Iterable[Any],
    runtime_bindings: Iterable[Any],
) -> None:
    for model_tool in model_tools:
        model_tool_id = getattr(model_tool, "model_tool_id", None)
        if not model_tool_id or not str(model_tool_id).strip():
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Module {entry.module_name} ({entry.label}) has invalid manifest - "
                "ModelToolDef missing model_tool_id",
                {"module_name": entry.module_name, "label": entry.label},
            )
    for runtime_binding in runtime_bindings:
        runtime_binding_id = getattr(runtime_binding, "runtime_binding_id", None)
        model_tool_id = getattr(runtime_binding, "model_tool_id", None)
        if not runtime_binding_id or not str(runtime_binding_id).strip():
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Module {entry.module_name} ({entry.label}) has invalid manifest - "
                "RuntimeBindingDef missing runtime_binding_id",
                {"module_name": entry.module_name, "label": entry.label},
            )
        if not model_tool_id or not str(model_tool_id).strip():
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Module {entry.module_name} ({entry.label}) has invalid manifest - "
                "RuntimeBindingDef missing model_tool_id",
                {"module_name": entry.module_name, "label": entry.label},
            )


def wire_default_tool_registry_manager(
    *,
    tool_bootstrap_entries: tuple[_ToolBootstrapEntry, ...] | None = None,
) -> None:
    """Build and wire ToolRegistryManager from default modules (TPR-04)."""
    registry_manager = ToolRegistryManager()

    for entry in tool_bootstrap_entries or _TOOL_BOOTSTRAP_ENTRIES:
        if not _tool_bootstrap_entry_enabled(entry):
            continue

        if entry.kind == "provider":
            continue  # Providers don't have manifests

        try:
            module = importlib.import_module(entry.module_name)
        except ImportError:
            if entry.required:
                raise
            continue

        registrar, _registrar_module_name = _resolve_module_registrar(
            entry.module_name,
            module,
        )
        typed_registrar = _require_registrar_protocol(
            module_name=entry.module_name,
            label=entry.label,
            registrar=registrar,
        )
        if typed_registrar.is_provider_only:
            continue

        ctx = ToolRegisterContext(
            module_id=entry.label.lower().replace(" ", "_"),
            config=None,
            workspace_root=None,
            run_root=None,
            strict=False,
        )
        manifest = _validate_manifest_contract(
            module_name=entry.module_name,
            label=entry.label,
            is_provider_only=False,
            manifest=typed_registrar.get_manifest(ctx),
        )
        assert manifest is not None

        model_tools = getattr(manifest, "model_tools", ())
        runtime_bindings = getattr(manifest, "runtime_bindings", ())
        if not model_tools or not runtime_bindings:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Module {entry.module_name} ({entry.label}) has empty manifest - "
                f"model_tools={len(model_tools)}, runtime_bindings={len(runtime_bindings)}. "
                "Set registrar.is_provider_only=True for provider-only modules.",
                {"module_name": entry.module_name, "label": entry.label},
            )

        _validate_manifest_ids(
            entry=entry, model_tools=model_tools, runtime_bindings=runtime_bindings
        )

        registry_manager.register_module_manifest(
            manifest, source_module=entry.module_name
        )

    registry_manager.compile()
    _emit_contract_drift_report(registry_manager)
    set_registry_manager(registry_manager)
    set_registry(None)
