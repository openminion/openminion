import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Mapping

from openminion.base.config.env import resolve_environment_config
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.constants import TOOL_BOOTSTRAP_STATUS_SKIPPED_GATE
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
    _dynamic_tool_bootstrap_entries,
    _entry_enabled,
    _entry_enabled_for_runtime_config,
)
from .registration import (
    _register_provider_plugin,
    _register_tool_entry,
    _validate_manifest_contract,
    _require_registrar_protocol,
    _resolve_module_registrar,
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


def _set_blockchain_contract_omissions(
    registry_manager: ToolRegistryManager,
    config: Any | None,
) -> None:
    runtime_cfg = getattr(config, "runtime", config)
    tools_cfg = getattr(runtime_cfg, "tools", None)
    blockchain_cfg = getattr(tools_cfg, "blockchain", None)
    if blockchain_cfg and getattr(blockchain_cfg, "enabled", False):
        return

    from openminion.modules.tool.contracts.model_ids import (
        MODEL_BLOCKCHAIN_INSPECT,
        MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
        MODEL_BLOCKCHAIN_SEND_TRANSACTION,
    )
    from openminion.modules.tool.contracts.runtime_ids import (
        RUNTIME_BLOCKCHAIN_INSPECT,
        RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
        RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
    )

    registry_manager.set_expected_contract_omissions(
        model_ids={
            MODEL_BLOCKCHAIN_INSPECT,
            MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
            MODEL_BLOCKCHAIN_SEND_TRANSACTION,
        },
        runtime_ids={
            RUNTIME_BLOCKCHAIN_INSPECT,
            RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
            RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
        },
    )


def _emit_contract_drift_report(
    registry_manager: ToolRegistryManager,
    *,
    config: Any | None = None,
) -> ToolContractDriftReport:
    from openminion.modules.tool.contracts.model_ids import (
        MODEL_BLOCKCHAIN_INSPECT,
        MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
        MODEL_BLOCKCHAIN_SEND_TRANSACTION,
    )
    from openminion.modules.tool.contracts.runtime_ids import (
        RUNTIME_BLOCKCHAIN_INSPECT,
        RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
        RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
    )

    runtime_cfg = getattr(config, "runtime", config)
    tools_cfg = getattr(runtime_cfg, "tools", None)
    blockchain_cfg = getattr(tools_cfg, "blockchain", None)
    blockchain_enabled = bool(
        blockchain_cfg and getattr(blockchain_cfg, "enabled", False)
    )
    report = registry_manager.contract_drift_report(
        expected_missing_model_ids=(
            set()
            if blockchain_enabled
            else {
                MODEL_BLOCKCHAIN_INSPECT,
                MODEL_BLOCKCHAIN_PREPARE_TRANSACTION,
                MODEL_BLOCKCHAIN_SEND_TRANSACTION,
            }
        ),
        expected_missing_runtime_ids=(
            set()
            if blockchain_enabled
            else {
                RUNTIME_BLOCKCHAIN_INSPECT,
                RUNTIME_BLOCKCHAIN_PREPARE_TRANSACTION,
                RUNTIME_BLOCKCHAIN_SEND_TRANSACTION,
            }
        ),
    )
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
                {"payload": payload},
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
    _set_blockchain_contract_omissions(registry_manager, config)
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

        bootstrap_records.append(
            _register_tool_entry(
                registry,
                registry_manager,
                entry=entry,
                config=config,
                workspace_root=workspace_path,
                run_root=run_path,
                strict_required=strict,
                context_strict=strict and entry.required,
            )
        )

    registry_manager.set_runtime_tool_schemas(_collect_runtime_tool_schemas(registry))
    registry_manager.compile()
    contract_drift_report = _emit_contract_drift_report(
        registry_manager,
        config=config,
    )

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
        if not _entry_enabled(entry):
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
