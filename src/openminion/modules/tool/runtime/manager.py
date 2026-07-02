from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.contracts import (
    ALL_MODEL_TOOL_IDS_SET,
    ALL_RUNTIME_BINDING_IDS_SET,
    ModelToolDef,
    ProviderToolSpec,
    ToolBindingManifest,
    is_valid_model_tool_id,
    is_valid_runtime_binding_id,
    normalize_raw_model_tool_name,
    strip_tool_wrapper_prefix,
    validate_manifest,
)


@dataclass(frozen=True)
class _CompiledBindings:
    model_tools_by_id: dict[str, ModelToolDef]
    schema_by_model_tool_id: dict[str, dict[str, Any]]
    model_to_runtime_binding_id: dict[str, str]
    runtime_binding_to_candidates: dict[str, tuple[str, ...]]
    model_input_to_model_tool_id: dict[str, str]
    model_input_to_model_tool_id_lower: dict[str, str]
    raw_to_model_tool_id: dict[str, str]
    raw_to_model_tool_id_lower: dict[str, str]


@dataclass(frozen=True)
class ToolContractDriftReport:
    model_tool_ids_missing_from_manifests: tuple[str, ...]
    model_tool_ids_missing_from_contracts: tuple[str, ...]
    runtime_binding_ids_missing_from_manifests: tuple[str, ...]
    runtime_binding_ids_missing_from_contracts: tuple[str, ...]

    @property
    def has_drift(self) -> bool:
        return any(
            (
                self.model_tool_ids_missing_from_manifests,
                self.model_tool_ids_missing_from_contracts,
                self.runtime_binding_ids_missing_from_manifests,
                self.runtime_binding_ids_missing_from_contracts,
            )
        )


class ToolRegistryManager:
    """Compiles tool-binding manifests into canonical lookup maps."""

    def __init__(self) -> None:
        self._manifests: list[ToolBindingManifest] = []
        self._compiled: _CompiledBindings | None = None
        self._runtime_schema_by_tool_name: dict[str, dict[str, Any]] = {}

    def register_manifest(self, manifest: ToolBindingManifest) -> None:
        validate_manifest(manifest)
        self._manifests.append(manifest)
        self._compiled = None

    def register_module_manifest(
        self, manifest: ToolBindingManifest, *, source_module: str
    ) -> None:
        """TMR-A02: Register manifest with module source tracking."""
        validate_manifest(manifest)
        # Store module source in manifest metadata if not already set
        if not manifest.module_id:
            manifest.module_id = source_module
        self._manifests.append(manifest)
        self._compiled = None

    def set_runtime_tool_schemas(
        self,
        schema_by_runtime_tool_name: Mapping[str, Mapping[str, Any]] | None,
    ) -> None:
        normalized: dict[str, dict[str, Any]] = {}
        for tool_name, schema in (schema_by_runtime_tool_name or {}).items():
            key = str(tool_name or "").strip()
            if not key:
                continue
            if isinstance(schema, Mapping):
                normalized[key] = dict(schema)
        self._runtime_schema_by_tool_name = normalized
        self._compiled = None

    def compile(self) -> None:
        model_tools_by_id: dict[str, ModelToolDef] = {}
        schema_by_model_tool_id: dict[str, dict[str, Any]] = {}
        model_to_runtime_binding_id: dict[str, str] = {}
        runtime_binding_to_candidates: dict[str, tuple[str, ...]] = {}
        model_input_to_model_tool_id: dict[str, str] = {}
        raw_to_model_tool_id: dict[str, str] = {}
        runtime_candidate_owners: dict[str, set[str]] = {}

        def _claim_raw(raw_name: str, model_tool_id: str, *, source: str) -> None:
            token = str(raw_name or "").strip()
            if not token:
                return
            existing = raw_to_model_tool_id.get(token)
            if existing is None:
                raw_to_model_tool_id[token] = model_tool_id
                return
            if existing != model_tool_id:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Raw name collision ({source}): {token!r} maps to both "
                    f"{existing!r} and {model_tool_id!r}",
                    {
                        "source": source,
                        "token": token,
                        "existing": existing,
                        "new": model_tool_id,
                    },
                )

        def _claim_model_input(
            raw_name: str, model_tool_id: str, *, source: str
        ) -> None:
            token = str(raw_name or "").strip()
            if not token:
                return
            existing = model_input_to_model_tool_id.get(token)
            if existing is None:
                model_input_to_model_tool_id[token] = model_tool_id
                return
            if existing != model_tool_id:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"Model-input raw name collision ({source}): {token!r} maps to both "
                    f"{existing!r} and {model_tool_id!r}",
                    {
                        "source": source,
                        "token": token,
                        "existing": existing,
                        "new": model_tool_id,
                    },
                )

        for manifest in self._manifests:
            module_id = str(manifest.module_id or "").strip() or "unknown"
            for model_tool in manifest.model_tools:
                model_tool_id = str(model_tool.model_tool_id or "").strip()
                if model_tool_id in model_tools_by_id:
                    raise ToolRuntimeError(
                        "INVALID_ARGUMENT",
                        f"Duplicate model_tool_id across manifests: {model_tool_id!r} "
                        f"(module={module_id})",
                        {"module_id": module_id, "model_tool_id": model_tool_id},
                    )
                model_tools_by_id[model_tool_id] = model_tool
                _claim_model_input(
                    model_tool_id, model_tool_id, source=f"{module_id}.model_tools"
                )
                _claim_raw(
                    model_tool_id, model_tool_id, source=f"{module_id}.model_tools"
                )
                for alias in model_tool.aliases:
                    _claim_model_input(
                        alias, model_tool_id, source=f"{module_id}.model_tools.aliases"
                    )
                    _claim_raw(
                        alias, model_tool_id, source=f"{module_id}.model_tools.aliases"
                    )

            for binding in manifest.runtime_bindings:
                runtime_binding_id = str(binding.runtime_binding_id or "").strip()
                model_tool_id = str(binding.model_tool_id or "").strip()
                if model_tool_id in model_to_runtime_binding_id:
                    raise ToolRuntimeError(
                        "INVALID_ARGUMENT",
                        f"Duplicate model->binding assignment: {model_tool_id!r} "
                        f"(module={module_id})",
                        {"module_id": module_id, "model_tool_id": model_tool_id},
                    )
                if runtime_binding_id in runtime_binding_to_candidates:
                    raise ToolRuntimeError(
                        "INVALID_ARGUMENT",
                        f"Duplicate runtime_binding_id across manifests: {runtime_binding_id!r} "
                        f"(module={module_id})",
                        {
                            "module_id": module_id,
                            "runtime_binding_id": runtime_binding_id,
                        },
                    )
                model_to_runtime_binding_id[model_tool_id] = runtime_binding_id
                runtime_binding_to_candidates[runtime_binding_id] = tuple(
                    binding.runtime_candidates
                )

                for runtime_candidate in binding.runtime_candidates:
                    token = str(runtime_candidate or "").strip()
                    if not token:
                        continue
                    runtime_candidate_owners.setdefault(token, set()).add(model_tool_id)

        for model_tool_id, model_tool in model_tools_by_id.items():
            parameters = dict(model_tool.parameters or {})
            if not parameters:
                runtime_binding_id = model_to_runtime_binding_id.get(model_tool_id, "")
                for candidate in runtime_binding_to_candidates.get(
                    runtime_binding_id, ()
                ):
                    candidate_schema = self._runtime_schema_by_tool_name.get(candidate)
                    if isinstance(candidate_schema, dict) and candidate_schema:
                        parameters = dict(candidate_schema)
                        break
            if not parameters:
                parameters = {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                }
            schema_by_model_tool_id[model_tool_id] = parameters

        for runtime_candidate, model_ids in runtime_candidate_owners.items():
            if len(model_ids) != 1:
                continue
            model_tool_id = next(iter(model_ids))
            _claim_raw(runtime_candidate, model_tool_id, source="runtime_candidates")

        raw_to_model_tool_id_lower: dict[str, str] = {}
        for raw_name, model_tool_id in raw_to_model_tool_id.items():
            lowered = raw_name.lower()
            existing = raw_to_model_tool_id_lower.get(lowered)
            if existing is None:
                raw_to_model_tool_id_lower[lowered] = model_tool_id
                continue
            if existing != model_tool_id:
                # Preserve deterministic behavior for exact-case lookups and avoid
                # ambiguous case-insensitive alias assignment.
                continue

        model_input_to_model_tool_id_lower: dict[str, str] = {}
        for raw_name, model_tool_id in model_input_to_model_tool_id.items():
            lowered = raw_name.lower()
            existing = model_input_to_model_tool_id_lower.get(lowered)
            if existing is None:
                model_input_to_model_tool_id_lower[lowered] = model_tool_id
                continue
            if existing != model_tool_id:
                continue

        self._compiled = _CompiledBindings(
            model_tools_by_id=model_tools_by_id,
            schema_by_model_tool_id=schema_by_model_tool_id,
            model_to_runtime_binding_id=model_to_runtime_binding_id,
            runtime_binding_to_candidates=runtime_binding_to_candidates,
            model_input_to_model_tool_id=model_input_to_model_tool_id,
            model_input_to_model_tool_id_lower=model_input_to_model_tool_id_lower,
            raw_to_model_tool_id=raw_to_model_tool_id,
            raw_to_model_tool_id_lower=raw_to_model_tool_id_lower,
        )

    def normalize_raw_name(self, raw: str) -> str | None:
        compiled = self._ensure_compiled()
        raw_token = str(raw or "").strip()
        if not raw_token:
            return None

        for token in (raw_token, strip_tool_wrapper_prefix(raw_token)):
            token = str(token or "").strip()
            if not token:
                continue

            direct = compiled.raw_to_model_tool_id.get(token)
            if direct:
                return direct

            lower = compiled.raw_to_model_tool_id_lower.get(token.lower())
            if lower:
                return lower

            canonical = normalize_raw_model_tool_name(token)
            if canonical and canonical in compiled.model_to_runtime_binding_id:
                return canonical
        return None

    def normalize_model_input_name(self, raw: str) -> str | None:
        """Normalize only canonical model-facing IDs and manifest-declared aliases."""
        compiled = self._ensure_compiled()
        raw_token = str(raw or "").strip()
        if not raw_token:
            return None

        for token in (raw_token, strip_tool_wrapper_prefix(raw_token)):
            token = str(token or "").strip()
            if not token:
                continue

            direct = compiled.model_input_to_model_tool_id.get(token)
            if direct:
                return direct

            lower = compiled.model_input_to_model_tool_id_lower.get(token.lower())
            if lower:
                return lower

            canonical = normalize_raw_model_tool_name(token)
            if canonical and is_valid_model_tool_id(canonical):
                return canonical
        return None

    def resolve_binding(self, model_tool_id: str) -> str | None:
        compiled = self._ensure_compiled()
        canonical = (
            self.normalize_raw_name(model_tool_id) or str(model_tool_id or "").strip()
        )
        if not canonical:
            return None
        return compiled.model_to_runtime_binding_id.get(canonical)

    def runtime_candidates(self, runtime_binding_id: str) -> tuple[str, ...]:
        compiled = self._ensure_compiled()
        token = str(runtime_binding_id or "").strip()
        if not token:
            return ()
        return compiled.runtime_binding_to_candidates.get(token, ())

    def runtime_binding_policy_defaults(self) -> dict[str, tuple[str, tuple[str, ...]]]:
        """Expose manifest-backed runtime binding defaults for config overlays."""
        compiled = self._ensure_compiled()
        defaults: dict[str, tuple[str, tuple[str, ...]]] = {}
        for binding_id, candidates in compiled.runtime_binding_to_candidates.items():
            ordered = tuple(
                str(item).strip() for item in candidates if str(item).strip()
            )
            if ordered:
                defaults[binding_id] = (ordered[0], ordered[1:])
        return defaults

    def model_to_runtime_binding_map(self) -> dict[str, str]:
        """Expose canonical model_tool_id -> runtime_binding_id map."""
        compiled = self._ensure_compiled()
        return dict(compiled.model_to_runtime_binding_id)

    def model_to_runtime_tool_map(
        self,
        available_runtime_tools: Iterable[str] | None = None,
    ) -> dict[str, str]:
        """Expose canonical model_tool_id -> selected runtime tool map."""
        compiled = self._ensure_compiled()
        available: set[str] | None = None
        if available_runtime_tools is not None:
            available = {
                str(item).strip()
                for item in available_runtime_tools
                if str(item).strip()
            }

        out: dict[str, str] = {}
        for model_tool_id, runtime_binding_id in sorted(
            compiled.model_to_runtime_binding_id.items()
        ):
            candidates = compiled.runtime_binding_to_candidates.get(
                runtime_binding_id, ()
            )
            if not candidates:
                continue
            if available is None:
                out[model_tool_id] = candidates[0]
                continue
            selected = next((item for item in candidates if item in available), "")
            if selected:
                out[model_tool_id] = selected
        return out

    def model_tool_catalog(self) -> tuple[tuple[str, str], ...]:
        """Expose canonical model-facing catalog rows as `(tool_id, description)`."""
        compiled = self._ensure_compiled()
        rows: list[tuple[str, str]] = []
        for model_tool_id in sorted(compiled.model_tools_by_id.keys()):
            tool_def = compiled.model_tools_by_id[model_tool_id]
            rows.append(
                (
                    model_tool_id,
                    str(getattr(tool_def, "description", "") or "").strip(),
                )
            )
        return tuple(rows)

    def model_runtime_dispatch_map(
        self,
        available_runtime_tools: Iterable[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        """Expose canonical model -> runtime dispatch metadata."""
        compiled = self._ensure_compiled()
        available: set[str] | None = None
        if available_runtime_tools is not None:
            available = {
                str(item).strip()
                for item in available_runtime_tools
                if str(item).strip()
            }

        out: dict[str, dict[str, object]] = {}
        for model_tool_id, runtime_binding_id in sorted(
            compiled.model_to_runtime_binding_id.items()
        ):
            all_candidates = list(
                compiled.runtime_binding_to_candidates.get(runtime_binding_id, ())
            )
            if available is None:
                selected = all_candidates[0] if all_candidates else ""
                effective_candidates = list(all_candidates)
            else:
                effective_candidates = [
                    item for item in all_candidates if item in available
                ]
                selected = effective_candidates[0] if effective_candidates else ""
            out[model_tool_id] = {
                "runtime_binding_id": runtime_binding_id,
                "runtime_tool_name": selected,
                "runtime_candidates": effective_candidates,
            }
        return out

    def model_provider_specs(
        self,
        available_runtime_tools: set[str],
    ) -> list[ProviderToolSpec]:
        compiled = self._ensure_compiled()
        available = {
            str(item).strip() for item in available_runtime_tools if str(item).strip()
        }
        specs: list[ProviderToolSpec] = []
        for model_tool_id in sorted(compiled.model_tools_by_id.keys()):
            runtime_binding_id = compiled.model_to_runtime_binding_id.get(model_tool_id)
            if not runtime_binding_id:
                continue
            runtime_candidates = [
                candidate
                for candidate in compiled.runtime_binding_to_candidates.get(
                    runtime_binding_id, ()
                )
                if candidate in available
            ]
            if not runtime_candidates:
                continue
            model_tool = compiled.model_tools_by_id[model_tool_id]
            description = str(model_tool.description or "").strip() or model_tool_id
            parameters = self.schema_for(model_tool_id=model_tool_id)
            specs.append(
                ProviderToolSpec(
                    name=model_tool_id,
                    description=description,
                    parameters=parameters,
                )
            )
        return specs

    def schema_for(self, model_tool_id: str) -> dict[str, Any]:
        compiled = self._ensure_compiled()
        canonical = (
            self.normalize_raw_name(model_tool_id) or str(model_tool_id or "").strip()
        )
        if not canonical:
            return {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            }
        schema = compiled.schema_by_model_tool_id.get(canonical)
        if isinstance(schema, dict):
            return dict(schema)
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }

    def _ensure_compiled(self) -> _CompiledBindings:
        if self._compiled is None:
            self.compile()
        assert self._compiled is not None
        return self._compiled

    def contract_drift_report(self) -> ToolContractDriftReport:
        """Compare compiled manifest IDs against canonical contract constants."""
        compiled = self._ensure_compiled()
        compiled_model_ids = set(compiled.model_to_runtime_binding_id.keys())
        compiled_runtime_binding_ids = set(
            compiled.model_to_runtime_binding_id.values()
        )

        return ToolContractDriftReport(
            model_tool_ids_missing_from_manifests=tuple(
                sorted(ALL_MODEL_TOOL_IDS_SET - compiled_model_ids)
            ),
            model_tool_ids_missing_from_contracts=tuple(
                sorted(
                    model_tool_id
                    for model_tool_id in compiled_model_ids
                    if not is_valid_model_tool_id(model_tool_id)
                )
            ),
            runtime_binding_ids_missing_from_manifests=tuple(
                sorted(ALL_RUNTIME_BINDING_IDS_SET - compiled_runtime_binding_ids)
            ),
            runtime_binding_ids_missing_from_contracts=tuple(
                sorted(
                    runtime_binding_id
                    for runtime_binding_id in compiled_runtime_binding_ids
                    if not is_valid_runtime_binding_id(runtime_binding_id)
                )
            ),
        )


def build_default_tool_registry_manager(
    extra_manifests: Iterable[ToolBindingManifest] | None = None,
) -> ToolRegistryManager:
    """Build manager from extra manifests only (TMR-E01).

    Module manifests are loaded via bootstrap, not this function.
    """
    manager = ToolRegistryManager()
    for manifest in tuple(extra_manifests or ()):
        manager.register_manifest(manifest)
    manager.compile()
    return manager
