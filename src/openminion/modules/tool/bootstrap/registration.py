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


class _ManifestCandidateValidationError(RuntimeError):
    """Raised when manifest runtime candidates do not map to registered tools."""


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

    token = str(module_name or "").strip()
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
    manifest: Any,
    registry: ToolRegistry,
) -> None:
    """Validate that manifest runtime candidates are present in registered tools.

    enforce this for all loaded non-provider-only modules, regardless of
    bootstrap `required` flag.
    """
    available_tools = set(registry.list().keys())
    missing: list[tuple[str, str]] = []
    for runtime_binding in getattr(manifest, "runtime_bindings", ()) or ():
        runtime_binding_id = str(
            getattr(runtime_binding, "runtime_binding_id", "") or ""
        ).strip()
        for candidate in getattr(runtime_binding, "runtime_candidates", ()) or ():
            token = str(candidate or "").strip()
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


def _is_empty_provider_only_manifest(manifest: Any) -> bool:
    model_tools = getattr(manifest, "model_tools", None)
    runtime_bindings = getattr(manifest, "runtime_bindings", None)
    if model_tools is None or runtime_bindings is None:
        return False
    return len(tuple(model_tools)) == 0 and len(tuple(runtime_bindings)) == 0


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


def _register_module_plugin(
    registry: ToolRegistry,
    *,
    module_name: str,
    label: str,
    required: bool = False,
) -> _ToolBootstrapRecord:
    from openminion.modules.tool.runtime.plugins import load_plugins

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        if required:
            raise
        logger.debug("%s plugin unavailable (%s): %s", label, module_name, exc)
        return _ToolBootstrapRecord(
            kind="tool",
            module_name=module_name,
            label=label,
            required=required,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_IMPORT_ERROR,
            error=str(exc),
        )

    before = len(registry.list())
    load_plugins(registry, module)
    added = [
        name
        for name in registry.list().keys()
        if name not in list(registry.list().keys())[:before]
    ]

    logger.info("%s: registered module path (%s)", label, module_name)
    return _ToolBootstrapRecord(
        kind="tool",
        module_name=module_name,
        label=label,
        required=required,
        gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
        enabled=True,
        status=TOOL_BOOTSTRAP_STATUS_REGISTERED,
        added_runtime_tools=list(added) if added else None,
    )


def _register_provider_plugin(*, module_name: str, label: str) -> _ToolBootstrapRecord:
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        logger.debug("%s plugin unavailable (%s): %s", label, module_name, exc)
        return _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_IMPORT_ERROR,
            error=str(exc),
        )

    register_fn = getattr(module, "register", None)
    if register_fn is None:
        return _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_NO_REGISTER,
        )

    try:
        register_fn()
        logger.info("%s: registered provider module path (%s)", label, module_name)
        return _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_REGISTERED,
        )
    except Exception as exc:
        logger.warning(
            "%s provider registration failed (%s): %s", label, module_name, exc
        )
        return _ToolBootstrapRecord(
            kind="provider",
            module_name=module_name,
            label=label,
            required=False,
            gate=TOOL_BOOTSTRAP_GATE_ALWAYS,
            enabled=True,
            status=TOOL_BOOTSTRAP_STATUS_REGISTER_FAILED,
            error=str(exc),
        )


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

        prepared_state = _prepare_tool_register_state(entry=entry, config=None)
        ctx = ToolRegisterContext(
            module_id=entry.label.lower().replace(" ", "_"),
            config=None,
            workspace_root=None,
            run_root=None,
            prepared_state=prepared_state,
            strict=False,
        )

        try:
            module = importlib.import_module(entry.module_name)
        except ImportError as exc:
            if entry.required:
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
            if entry.required:
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

    return bootstrap_records
