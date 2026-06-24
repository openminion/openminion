from dataclasses import dataclass
from typing import Any
import warnings

from ..errors import ToolRuntimeError
from .model_ids import is_valid_model_tool_id
from .runtime_ids import is_valid_runtime_binding_id


@dataclass(frozen=True)
class ModelToolDef:
    model_tool_id: str
    description: str
    parameters: dict[str, Any]
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.parameters, dict) and self.parameters:
            warnings.warn(
                "ModelToolDef.parameters is deprecated; schema is derived at runtime "
                "via ToolRegistryManager.schema_for().",
                DeprecationWarning,
                stacklevel=2,
            )


@dataclass(frozen=True)
class RuntimeBindingDef:
    runtime_binding_id: str
    model_tool_id: str
    runtime_candidates: tuple[str, ...]


@dataclass(frozen=True)
class ToolBindingManifest:
    module_id: str
    model_tools: tuple[ModelToolDef, ...]
    runtime_bindings: tuple[RuntimeBindingDef, ...]


def validate_manifest(manifest: ToolBindingManifest) -> None:
    module_id = str(manifest.module_id or "").strip()
    if not module_id:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "ToolBindingManifest.module_id must be non-empty",
        )

    seen_model_ids: set[str] = set()
    for model_tool in manifest.model_tools:
        model_tool_id = str(model_tool.model_tool_id or "").strip()
        if not model_tool_id:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: model_tool_id must be non-empty",
                {"module_id": module_id},
            )
        if not is_valid_model_tool_id(model_tool_id):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: non-canonical model_tool_id={model_tool_id!r}",
                {"module_id": module_id, "model_tool_id": model_tool_id},
            )
        if model_tool_id in seen_model_ids:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: duplicate model_tool_id={model_tool_id!r}",
                {"module_id": module_id, "model_tool_id": model_tool_id},
            )
        seen_model_ids.add(model_tool_id)

    seen_binding_ids: set[str] = set()
    for binding in manifest.runtime_bindings:
        binding_id = str(binding.runtime_binding_id or "").strip()
        if not binding_id:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: runtime_binding_id must be non-empty",
                {"module_id": module_id},
            )
        if not is_valid_runtime_binding_id(binding_id):
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: non-canonical runtime_binding_id={binding_id!r}",
                {"module_id": module_id, "runtime_binding_id": binding_id},
            )
        if binding_id in seen_binding_ids:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: duplicate runtime_binding_id={binding_id!r}",
                {"module_id": module_id, "runtime_binding_id": binding_id},
            )
        seen_binding_ids.add(binding_id)
        model_tool_id = str(binding.model_tool_id or "").strip()
        if not model_tool_id:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: binding.model_tool_id must be non-empty",
                {"module_id": module_id, "runtime_binding_id": binding_id},
            )
        if model_tool_id not in seen_model_ids:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"{module_id}: runtime binding {binding_id!r} references unknown "
                f"model_tool_id={model_tool_id!r}",
                {
                    "module_id": module_id,
                    "runtime_binding_id": binding_id,
                    "model_tool_id": model_tool_id,
                },
            )
