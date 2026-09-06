import importlib
import logging
from typing import Any

from openminion.modules.tool.constants import (
    TOOL_BOOTSTRAP_GATE_ALWAYS,
    TOOL_BOOTSTRAP_STATUS_IMPORT_ERROR,
    TOOL_BOOTSTRAP_STATUS_NO_REGISTER,
    TOOL_BOOTSTRAP_STATUS_REGISTERED,
    TOOL_BOOTSTRAP_STATUS_REGISTER_FAILED,
    TOOL_BOOTSTRAP_STATUS_REGISTRAR_FAILED,
    TOOL_BOOTSTRAP_STATUS_SKIPPED_GATE,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.contracts import ToolBindingManifest
from openminion.modules.tool.runtime.manager import ToolRegistryManager
from openminion.modules.tool.runtime.registrar import (
    ToolModuleRegistrar,
    ToolRegisterContext,
)
from openminion.modules.tool.registry import ToolRegistry
from .entries import (
    _TOOL_BOOTSTRAP_ENTRIES,
    _ToolBootstrapEntry,
    _ToolBootstrapRecord,
    _apply_dynamic_runtime_ownership,
    _dynamic_tool_bootstrap_entries,
    _entry_enabled,
    _prepare_tool_register_state,
    _prepared_state_record_details,
)

logger = logging.getLogger("openminion.modules.tool.bootstrap")
_PROVIDER_MODULE_RECORDS: dict[str, _ToolBootstrapRecord] = {}


class _ManifestCandidateValidationError(RuntimeError):
    """Raised when manifest runtime candidates do not map to registered tools."""


class _ExternalPluginContractError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


_TOOL_PACKAGE_MODULE_ID_COMPATIBILITY = {
    "reaction": "reactions",
    "todo": "plan",
}


def _validate_tool_package_module_id(module_name: str, module_id: str) -> None:
    prefix = "openminion.tools."
    if not module_name.startswith(prefix):
        return
    package_path = module_name[len(prefix) :].split(".")
    if len(package_path) != 1:
        return
    package_leaf = package_path[0]
    if package_leaf.startswith("_"):
        return
    expected = _TOOL_PACKAGE_MODULE_ID_COMPATIBILITY.get(package_leaf, package_leaf)
    if module_id != expected:
        raise TypeError(  # allow-bare-raise: registrar package/module contract guard
            f"Module {module_name} REGISTRAR.module_id={module_id!r} must match "
            f"package owner {expected!r}"
        )


def _require_registrar_protocol(
    *,
    module_name: str,
    label: str,
    registrar: Any | None,
) -> ToolModuleRegistrar:
    if registrar is None:
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) missing REGISTRAR implementing "
            "ToolModuleRegistrar"
        )

    if not hasattr(registrar, "module_id"):
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) REGISTRAR missing required attribute "
            "'module_id'"
        )
    module_id = getattr(registrar, "module_id", None)
    if not isinstance(module_id, str) or not module_id.strip():
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) REGISTRAR.module_id must be a non-empty "
            "string"
        )
    _validate_tool_package_module_id(module_name, module_id)

    if not hasattr(registrar, "is_provider_only"):
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) REGISTRAR missing required attribute "
            "'is_provider_only'"
        )
    is_provider_only = getattr(registrar, "is_provider_only", None)
    if not isinstance(is_provider_only, bool):
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) REGISTRAR.is_provider_only must be bool"
        )

    register_fn = getattr(registrar, "register", None)
    if not callable(register_fn):
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) REGISTRAR.register must be callable"
        )

    get_manifest_fn = getattr(registrar, "get_manifest", None)
    if not callable(get_manifest_fn):
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) REGISTRAR.get_manifest must be callable"
        )

    if not isinstance(registrar, ToolModuleRegistrar):
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) REGISTRAR does not conform to "
            "ToolModuleRegistrar"
        )
    return registrar


def _resolve_module_registrar(
    module_name: str,
    module: Any,
) -> tuple[Any | None, str]:
    registrar: Any | None = getattr(module, "REGISTRAR", None)
    if registrar is not None:
        return registrar, module_name

    token = module_name.strip()
    fallback_module = ""
    if token.endswith(".plugin"):
        fallback_module = token[: -len(".plugin")]
    elif token.endswith(".tool"):
        fallback_module = token[: -len(".tool")]

    if not fallback_module:
        return None, token

    try:
        pkg = importlib.import_module(fallback_module)
    except ImportError:
        return None, token

    registrar = getattr(pkg, "REGISTRAR", None)
    if registrar is None:
        return None, token
    return registrar, fallback_module


def _validate_manifest_runtime_candidates(
    *,
    module_name: str,
    label: str,
    manifest: ToolBindingManifest,
    registry: ToolRegistry,
) -> None:
    """Validate that manifest runtime candidates are present in registered tools.

    enforce this for all loaded non-provider-only modules, regardless of
    bootstrap `required` flag.
    """
    available_tools = set(registry.list().keys())
    missing: list[tuple[str, str]] = []
    for runtime_binding in manifest.runtime_bindings:
        runtime_binding_id = runtime_binding.runtime_binding_id.strip()
        for candidate in runtime_binding.runtime_candidates:
            token = candidate.strip()
            if not token:
                continue
            if token not in available_tools:
                missing.append((runtime_binding_id, token))

    if not missing:
        return

    sample = ", ".join(f"{binding}:{candidate}" for binding, candidate in missing[:5])
    raise _ManifestCandidateValidationError(
        f"Module {module_name} ({label}) manifest references runtime candidates "
        f"not registered in ToolRegistry ({sample})."
    )


def _is_empty_provider_only_manifest(manifest: ToolBindingManifest) -> bool:
    return not manifest.model_tools and not manifest.runtime_bindings


def _validate_external_plugin_ownership(
    *,
    plugin_id: str,
    registrar: ToolModuleRegistrar,
    manifest: ToolBindingManifest | None,
) -> None:
    model_prefix = f"plugin.{plugin_id}."
    binding_prefix = f"runtime.plugin.{plugin_id}."
    if registrar.module_id != plugin_id or (
        manifest is not None and manifest.module_id != plugin_id
    ):
        raise _ExternalPluginContractError("owner_mismatch")
    if manifest is None:
        return
    if any(
        not item.model_tool_id.startswith(model_prefix) for item in manifest.model_tools
    ):
        raise _ExternalPluginContractError("namespace_invalid")
    for binding in manifest.runtime_bindings:
        if not binding.runtime_binding_id.startswith(binding_prefix):
            raise _ExternalPluginContractError("namespace_invalid")
        if any(
            not candidate.startswith(model_prefix)
            for candidate in binding.runtime_candidates
        ):
            raise _ExternalPluginContractError("namespace_invalid")


def _validate_manifest_contract(
    *,
    module_name: str,
    label: str,
    is_provider_only: bool,
    manifest: Any | None,
) -> ToolBindingManifest | None:
    if is_provider_only:
        if manifest is None:
            return None
        if not isinstance(manifest, ToolBindingManifest):
            raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
                f"Module {module_name} ({label}) provider-only "
                "REGISTRAR.get_manifest() must return None or ToolBindingManifest."
            )
        if not _is_empty_provider_only_manifest(manifest):
            raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
                f"Module {module_name} ({label}) provider-only "
                "REGISTRAR.get_manifest() must return None or empty ToolBindingManifest."
            )
        return None

    if manifest is None:
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) non-provider "
            "REGISTRAR.get_manifest() returned None; expected ToolBindingManifest."
        )
    if not isinstance(manifest, ToolBindingManifest):
        raise TypeError(  # allow-bare-raise: defensive type guard on plugin REGISTRAR shape
            f"Module {module_name} ({label}) non-provider "
            "REGISTRAR.get_manifest() must return ToolBindingManifest."
        )
    return manifest


def _register_provider_plugin(*, module_name: str, label: str) -> _ToolBootstrapRecord:
    cached = _PROVIDER_MODULE_RECORDS.get(module_name)
    if cached is not None:
        return cached
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        logger.debug("%s plugin unavailable (%s): %s", label, module_name, exc)
        record = _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_IMPORT_ERROR,
            error=str(exc),
        )
        _PROVIDER_MODULE_RECORDS[module_name] = record
        return record

    register_fn = getattr(module, "register", None)
    if register_fn is None:
        record = _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_NO_REGISTER,
        )
        _PROVIDER_MODULE_RECORDS[module_name] = record
        return record

    try:
        register_fn()
        logger.info("%s: registered provider module path (%s)", label, module_name)
        record = _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_REGISTERED,
        )
        _PROVIDER_MODULE_RECORDS[module_name] = record
        return record
    except Exception as exc:
        logger.warning(
            "%s provider registration failed (%s): %s", label, module_name, exc
        )
        record = _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_REGISTER_FAILED,
            error=str(exc),
        )
        _PROVIDER_MODULE_RECORDS[module_name] = record
        return record


def _tool_entry_failure_record(
    entry: _ToolBootstrapEntry,
    *,
    status: str,
    error: str,
) -> _ToolBootstrapRecord:
    return _ToolBootstrapRecord(
        kind=entry.kind,
        module_name=entry.module_name,
        label=entry.label,
        required=entry.required,
        gate=entry.gate,
        enabled=True,
        status=status,
        error=error,
    )


def _import_tool_entry_module(
    entry: _ToolBootstrapEntry,
    *,
    strict_required: bool,
) -> tuple[Any | None, str]:
    try:
        return importlib.import_module(entry.module_name), ""
    except ImportError as exc:
        if entry.required and strict_required:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Required module {entry.module_name} not found: {exc}",
                {"module_name": entry.module_name},
            ) from exc
        logger.debug(
            "%s plugin unavailable (%s): %s", entry.label, entry.module_name, exc
        )
        return None, str(exc)


def _register_tool_entry(
    registry: ToolRegistry,
    registry_manager: ToolRegistryManager,
    *,
    entry: _ToolBootstrapEntry,
    config: Any | None,
    workspace_root: Any | None,
    run_root: Any | None,
    strict_required: bool,
    context_strict: bool = False,
) -> _ToolBootstrapRecord:
    prepared_state = _prepare_tool_register_state(entry=entry, config=config)
    ctx = ToolRegisterContext(
        module_id=entry.label.lower().replace(" ", "_"),
        config=config,
        workspace_root=workspace_root,
        run_root=run_root,
        prepared_state=prepared_state,
        strict=context_strict,
    )
    module, import_error = _import_tool_entry_module(
        entry, strict_required=strict_required
    )
    if module is None:
        return _tool_entry_failure_record(
            entry,
            status=TOOL_BOOTSTRAP_STATUS_IMPORT_ERROR,
            error=import_error,
        )

    registrar, registrar_module_name = _resolve_module_registrar(
        entry.module_name,
        module,
    )
    return _register_registrar(
        registry,
        registry_manager,
        entry=entry,
        registrar=registrar,
        registrar_module_name=registrar_module_name,
        ctx=ctx,
        prepared_state=prepared_state,
        strict_required=strict_required,
    )


def _register_registrar(
    registry: ToolRegistry,
    registry_manager: ToolRegistryManager,
    *,
    entry: _ToolBootstrapEntry,
    registrar: Any,
    registrar_module_name: str,
    ctx: ToolRegisterContext,
    prepared_state: Any,
    strict_required: bool,
) -> _ToolBootstrapRecord:
    try:
        typed_registrar = _require_registrar_protocol(
            module_name=entry.module_name,
            label=entry.label,
            registrar=registrar,
        )
        manifest = _validate_manifest_contract(
            module_name=entry.module_name,
            label=entry.label,
            is_provider_only=typed_registrar.is_provider_only,
            manifest=typed_registrar.get_manifest(ctx),
        )
        if entry.module_name.startswith("plugin:"):
            _validate_external_plugin_ownership(
                plugin_id=entry.module_name.removeprefix("plugin:"),
                registrar=typed_registrar,
                manifest=manifest,
            )
        if manifest is not None:
            registry_manager.register_module_manifest(
                manifest, source_module=entry.module_name
            )
        before_tools = set(registry.list())
        typed_registrar.register(registry, ctx)
        _apply_dynamic_runtime_ownership(
            registry=registry,
            prepared_state=prepared_state,
        )
        if manifest is not None:
            _validate_manifest_runtime_candidates(
                module_name=entry.module_name,
                label=entry.label,
                manifest=manifest,
                registry=registry,
            )
        prepared_tools, error_summary = _prepared_state_record_details(prepared_state)
        added_runtime_tools = sorted(
            (set(registry.list()) - before_tools) | set(prepared_tools or ())
        )
        manifest_candidates = (
            {
                candidate
                for binding in manifest.runtime_bindings
                for candidate in binding.runtime_candidates
            }
            if manifest is not None
            else set()
        )
        runtime_only_tools = sorted(set(added_runtime_tools) - manifest_candidates)
        if entry.module_name.startswith("plugin:") and runtime_only_tools:
            raise _ExternalPluginContractError("unbound_tool_contribution")
        logger.info(
            "%s: registered via REGISTRAR (%s)", entry.label, registrar_module_name
        )
        return _ToolBootstrapRecord(
            kind=entry.kind,
            module_name=entry.module_name,
            label=entry.label,
            required=entry.required,
            gate=entry.gate,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_REGISTERED,
            error=error_summary,
            added_runtime_tools=added_runtime_tools,
            runtime_only_tools=runtime_only_tools or None,
        )
    except Exception as exc:
        if entry.module_name.startswith("plugin:"):
            raise
        if isinstance(exc, TypeError):
            raise
        if isinstance(exc, _ManifestCandidateValidationError) or (
            entry.required and strict_required
        ):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Module {entry.module_name} REGISTRAR failed: {exc}",
                {"module_name": entry.module_name},
            ) from exc
        logger.warning(
            "%s REGISTRAR failed (%s): %s", entry.label, entry.module_name, exc
        )
        record = _tool_entry_failure_record(
            entry,
            status=TOOL_BOOTSTRAP_STATUS_REGISTRAR_FAILED,
            error=str(exc),
        )
        return record


def _register_external_registrar(
    registry: ToolRegistry,
    registry_manager: ToolRegistryManager,
    *,
    plugin_id: str,
    registrar: Any,
    config: Any | None,
    workspace_root: Any | None,
    run_root: Any | None,
) -> _ToolBootstrapRecord:
    entry = _ToolBootstrapEntry(
        kind="tool",
        module_name=f"plugin:{plugin_id}",
        label=plugin_id,
        required=True,
    )
    ctx = ToolRegisterContext(
        module_id=plugin_id,
        config=config,
        workspace_root=workspace_root,
        run_root=run_root,
        strict=True,
    )
    try:
        record = _register_registrar(
            registry,
            registry_manager,
            entry=entry,
            registrar=registrar,
            registrar_module_name=f"plugin:{plugin_id}",
            ctx=ctx,
            prepared_state=None,
            strict_required=True,
        )
        registry_manager.compile()
        return record
    except Exception as exc:
        if isinstance(exc, _ExternalPluginContractError):
            reason_code = exc.reason_code
        elif isinstance(exc, _ManifestCandidateValidationError):
            reason_code = "candidate_missing"
        elif isinstance(exc, ToolRuntimeError):
            reason_code = "runtime_collision"
        elif isinstance(exc, TypeError):
            reason_code = "registrar_invalid"
        elif isinstance(exc, ValueError):
            reason_code = "registration_failed"
        else:
            reason_code = "registration_failed"
        raise ToolRuntimeError(
            "PLUGIN_ACTIVATION_FAILED",
            "Plugin tool registration failed.",
            {
                "plugin_id": plugin_id,
                "stage": "tool_registration",
                "reason_code": reason_code,
            },
        ) from exc


def _bootstrap_default_registry(
    registry: ToolRegistry,
    registry_manager: ToolRegistryManager,
    *,
    modules_only: bool = False,
    tool_bootstrap_entries: tuple[_ToolBootstrapEntry, ...] | None = None,
) -> list[_ToolBootstrapRecord]:

    bootstrap_records: list[_ToolBootstrapRecord] = []

    for entry in _dynamic_tool_bootstrap_entries(
        None,
        tool_bootstrap_entries=tool_bootstrap_entries or _TOOL_BOOTSTRAP_ENTRIES,
    ):
        enabled = _entry_enabled(entry)
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
                config=None,
                workspace_root=None,
                run_root=None,
                strict_required=True,
            )
        )

    return bootstrap_records
