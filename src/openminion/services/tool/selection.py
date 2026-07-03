import fnmatch
import json
import logging
from typing import Any, Dict, List, Optional

from openminion.base.config import ToolSelectionConfig
from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.modules.tool.config import (
    TOOL_STUB_DESCRIPTION_MAX_CHARS as _TOOL_STUB_DESCRIPTION_MAX_CHARS,
)
from openminion.modules.tool.runtime.policy import ToolBindingPolicyManager
from openminion.modules.tool.contracts import normalize_raw_model_tool_name
from openminion.modules.tool.dispatch import _get_registry_manager
from openminion.modules.tool.registry import ToolRegistry
from .exposure import get_model_exposure_specs
from .records import (
    CANONICAL_CATEGORY_COMPAT_IDS,
    FilterOutcome as _FilterOutcome,
    PREFERRED_MODEL_TOOLS_BY_CATEGORY,
    READ_ONLY_BLOCKED_CATEGORIES,
    SchemaExposure,
    SelectionMode,
    SelectionResult,
    ShortlistPlan,
    ToolStub,
    ValidationError,
    create_validation_error as _create_validation_error,
    first_available_tool as _first_available_tool,
    selection_result_to_provider_specs as _selection_result_to_provider_specs,
    stub_to_provider_spec as _stub_to_provider_spec,
)

_REGISTRY_MANAGER_WARMED = False
_LOG = logging.getLogger(__name__)
create_validation_error, stub_to_provider_spec = (
    _create_validation_error,
    _stub_to_provider_spec,
)
selection_result_to_provider_specs = _selection_result_to_provider_specs
_READ_ONLY_BLOCKED_CATEGORIES = READ_ONLY_BLOCKED_CATEGORIES
_CANONICAL_CATEGORY_COMPAT_IDS = CANONICAL_CATEGORY_COMPAT_IDS
_PREFERRED_MODEL_TOOLS_BY_CATEGORY = PREFERRED_MODEL_TOOLS_BY_CATEGORY


class ToolSelectionService:
    def __init__(
        self,
        config: ToolSelectionConfig,
        registry: ToolRegistry,
    ) -> None:
        self._config = config
        self._registry = registry
        self._mode = SelectionMode(config.mode)
        self._schema_exposure = SchemaExposure(config.schema_exposure)
        self._identity_filter_cache: dict[str, _FilterOutcome] = {}
        # typed telemetry — mode_resolved.
        _LOG.info(
            "selection.mode_resolved",
            extra={
                "event_type": "selection.mode_resolved",
                "mode": self._mode.value,
                "source": "tool_selection.config",
            },
        )

    def _registry_specs(self, registry: Any | None = None) -> list[ProviderToolSpec]:
        target = registry or self._registry
        model_specs = list(get_model_exposure_specs(target))
        if model_specs:
            return model_specs
        _LOG.warning("registry_specs_unavailable")
        return []

    def _manager_schema_for(self, tool_name: str) -> dict[str, Any]:
        binding_manager_fn = getattr(self._registry, "_binding_manager", None)
        if not callable(binding_manager_fn):
            return {}
        try:
            manager = binding_manager_fn()
        except Exception:
            return {}
        if not callable(getattr(manager, "schema_for", None)):
            return {}
        try:
            schema = manager.schema_for(tool_name)
        except Exception:
            return {}
        if isinstance(schema, dict):
            normalized = dict(schema)
            if (
                normalized.get("type") == "object"
                and isinstance(normalized.get("properties"), dict)
                and not normalized.get("properties")
                and normalized.get("additionalProperties") is True
            ):
                return {}
            if (
                normalized.get("type") == "object"
                and not isinstance(normalized.get("properties"), dict)
                and normalized.get("additionalProperties") is True
            ):
                return {}
            return normalized
        return {}

    def select_tools(
        self,
        query: str,
        intent_categories: Optional[List[str]] = None,
        forced_category: Optional[str] = None,
        identity_tool_filter: dict[str, Any] | None = None,
        specs: list[ProviderToolSpec] | None = None,
        tool_use_type: str | None = None,
    ) -> SelectionResult:
        filter_payload = dict(identity_tool_filter or {})
        if tool_use_type and "tool_use" not in filter_payload:
            filter_payload["tool_use"] = str(tool_use_type).strip().lower()
        filter_outcome: _FilterOutcome | None = None
        filter_cache_hit = False
        if filter_payload:
            source_specs = (
                list(specs) if specs is not None else self._registry_specs(self._registry)
            )
            cache_key = self._identity_filter_cache_key(source_specs, filter_payload)
            filter_outcome = self._identity_filter_cache.get(cache_key)
            if filter_outcome is None:
                filter_outcome = self._apply_identity_tool_filter(
                    source_specs,
                    filter_payload,
                )
                self._identity_filter_cache[cache_key] = filter_outcome
            else:
                filter_cache_hit = True
            specs = list(filter_outcome.specs)

        result = self._route_selection(
            query,
            intent_categories=intent_categories,
            forced_category=forced_category,
            specs=specs,
        )
        if filter_outcome and filter_outcome.unresolved_category_count > 0:
            result.reason_codes.append(
                f"read_only:unresolved_categories:{filter_outcome.unresolved_category_count}"
            )
        if filter_payload:
            result.reason_codes.append(
                "identity_filter_cache_hit"
                if filter_cache_hit
                else "identity_filter_cache_miss"
            )
        return result

    def _route_selection(
        self,
        query: str,
        *,
        intent_categories: Optional[List[str]] = None,
        forced_category: Optional[str] = None,
        specs: Optional[List[ProviderToolSpec]] = None,
    ) -> SelectionResult:
        if forced_category and self._mode in (
            SelectionMode.DETERMINISTIC,
            SelectionMode.TYPED,
        ):
            return self._deterministic_selection(query, forced_category, specs=specs)

        if intent_categories and self._mode in (
            SelectionMode.DETERMINISTIC,
            SelectionMode.TYPED,
        ):
            primary_category = intent_categories[0]
            return self._deterministic_selection(query, primary_category, specs=specs)

        # No typed signal present. Post-TSSR behavior splits by mode:
        if self._mode == SelectionMode.DETERMINISTIC:
            return SelectionResult(
                mode="deterministic",
                shortlist=[],
                stubs=[],
                full_schema_tools=[],
                category=None,
                binding_source=None,
                fallback_used=False,
                token_estimate=0,
                reason_codes=["no_typed_signal"],
            )
        return self._full_catalog_selection(specs=specs)

    def get_primary_tool_for_category(self, category: str) -> Optional[str]:
        """Get the configured primary tool for a category (handles normalization)."""
        for key in self._selection_lookup_keys(category):
            if hasattr(self._config, "bindings") and key in self._config.bindings:
                primary = str(self._config.bindings.get(key) or "").strip()
                if primary:
                    canonical = self._canonical_model_tool_name(primary)
                    return canonical or primary

            if (
                hasattr(self._config, "capabilities")
                and key in self._config.capabilities
            ):
                candidate = self._config.capabilities[key].primary
                canonical = self._canonical_model_tool_name(candidate)
                return canonical or candidate
        return None

    def get_fallback_tools_for_category(self, category: str) -> List[str]:
        """Get configured fallback tools for a category (handles normalization)."""
        for key in self._selection_lookup_keys(category):
            if (
                hasattr(self._config, "bindings_fallback")
                and key in self._config.bindings_fallback
            ):
                fallback = [
                    str(item).strip()
                    for item in (self._config.bindings_fallback.get(key) or [])
                    if str(item).strip()
                ]
                return self._canonicalize_tool_chain(fallback)

            if (
                hasattr(self._config, "capabilities")
                and key in self._config.capabilities
            ):
                return self._canonicalize_tool_chain(
                    list(self._config.capabilities[key].fallback_tools)
                )
        return []

    def _selection_lookup_keys(self, category: str) -> list[str]:
        token = str(category or "").strip()
        if not token:
            return []

        keys: list[str] = []

        def _add(candidate: str) -> None:
            normalized = str(candidate or "").strip()
            if normalized and normalized not in keys:
                keys.append(normalized)

        _add(token)

        canonical_model = self._normalize_model_tool_token(token)
        if canonical_model:
            _add(canonical_model)
        return keys

    def runtime_binding_policies(self) -> dict[str, dict[str, Any]]:
        """Export runtime binding preference policy for execution context metadata."""
        manager = ToolBindingPolicyManager.from_tool_selection_config(self._config)
        return manager.runtime_binding_policies_payload()

    def runtime_binding_policy_metadata(self) -> dict[str, Any]:
        """Build execution metadata for runtime binding dispatch + fallback policy."""
        manager = ToolBindingPolicyManager.from_tool_selection_config(self._config)
        return manager.metadata_payload()

    def _deterministic_selection(
        self,
        query: str,
        category: str,
        *,
        specs: Optional[List[ProviderToolSpec]] = None,
    ) -> SelectionResult:
        del query
        normalized_category = category
        available_model_tools = self._available_model_tools(specs)
        category_primary = self.get_primary_tool_for_category(category)
        fallback_chain = self.get_fallback_tools_for_category(category)
        selected_tool, binding_source = self._select_bound_or_explicit_tool(
            category=category,
            category_primary=category_primary,
            fallback_chain=fallback_chain,
            available_model_tools=available_model_tools,
        )
        has_availability_index = bool(available_model_tools)
        if not selected_tool:
            selected_tool, binding_source = self._select_category_index_tool(
                category=normalized_category,
                available_model_tools=available_model_tools,
            )
        selected_tool = (
            self._coerce_runtime_alias_to_requested_category_tool(
                selected_tool=selected_tool,
                category=normalized_category,
            )
            or self._canonical_model_tool_name(selected_tool)
            or selected_tool
        )
        fallback_chain = self._canonicalize_tool_chain(fallback_chain)
        fallback_chain = [
            item
            for item in fallback_chain
            if item != selected_tool
            and (not has_availability_index or item in available_model_tools)
        ]

        if not selected_tool:
            return SelectionResult(
                mode="deterministic",
                shortlist=[],
                stubs=[],
                full_schema_tools=[],
                category=normalized_category,
                binding_source=None,
                fallback_used=False,
                token_estimate=0,
                reason_codes=[
                    "deterministic",
                    f"category:{normalized_category}",
                    "no_category_tool_match",
                ],
            )

        stub = self._generate_stub(selected_tool)
        reason_codes = ["deterministic", f"category:{normalized_category}"]
        if binding_source:
            reason_codes.append(f"binding_source:{binding_source}")

        return SelectionResult(
            mode="deterministic",
            shortlist=[selected_tool],
            stubs=[stub] if stub else [],
            full_schema_tools=[],
            category=normalized_category,
            binding_source=binding_source,
            fallback_used=binding_source == "capability_fallback",
            token_estimate=self._estimate_stub_tokens(stub) if stub else 0,
            reason_codes=reason_codes,
        )

    def _select_bound_or_explicit_tool(
        self,
        *,
        category: str,
        category_primary: str | None,
        fallback_chain: list[str],
        available_model_tools: set[str],
    ) -> tuple[str, str | None]:
        raw_category_token = str(category or "").strip()
        explicit_model_tool_id = (
            self._normalize_model_tool_token(raw_category_token) or ""
        )
        if self._explicit_category_is_available_tool(
            raw_category_token=raw_category_token,
            explicit_model_tool_id=explicit_model_tool_id,
            available_model_tools=available_model_tools,
        ):
            return explicit_model_tool_id, "explicit_model_tool"
        if category_primary:
            return category_primary, "capability_primary"
        selected = _first_available_tool(
            fallback_chain=fallback_chain,
            available_model_tools=available_model_tools,
        )
        return (selected, "capability_fallback") if selected else ("", None)

    def _explicit_category_is_available_tool(
        self,
        *,
        raw_category_token: str,
        explicit_model_tool_id: str,
        available_model_tools: set[str],
    ) -> bool:
        if not explicit_model_tool_id:
            return False
        is_explicit_tool = raw_category_token == explicit_model_tool_id
        if callable(getattr(self._registry, "provider_spec_for_name", None)):
            is_explicit_tool = (
                is_explicit_tool
                or self._registry.provider_spec_for_name(raw_category_token) is not None
            )
        return bool(
            is_explicit_tool
            and (
                not available_model_tools
                or explicit_model_tool_id in available_model_tools
            )
        )

    def _select_category_index_tool(
        self,
        *,
        category: str,
        available_model_tools: set[str],
    ) -> tuple[str, str | None]:
        model_tools_in_category = self._model_tools_for_category(
            category=category,
            available_model_tools=available_model_tools,
        )
        if model_tools_in_category:
            return (
                self._pick_category_default_model_tool(
                    category=category,
                    model_ids=model_tools_in_category,
                ),
                "category_index",
            )
        selected_tool = self._category_compat_model_tool_id(category)
        return (selected_tool, "category_index") if selected_tool else ("", None)

    def _available_model_tools(
        self, specs: Optional[List[ProviderToolSpec]] = None
    ) -> set[str]:
        source_specs = (
            list(specs) if specs is not None else self._registry_specs(self._registry)
        )
        available: set[str] = set()
        for spec in source_specs:
            token = str(spec.name or "").strip()
            if token:
                available.add(token)
        if available:
            return available

        runtime_tools = getattr(self._registry, "_tools", None)
        if isinstance(runtime_tools, dict):
            for runtime_name in runtime_tools.keys():
                token = str(runtime_name or "").strip()
                if token:
                    available.add(token)
        return available

    def _model_tools_for_category(
        self,
        *,
        category: str,
        available_model_tools: set[str],
    ) -> list[str]:
        token = str(category or "").strip()
        if not token:
            return sorted(available_model_tools)

        matches: list[str] = []
        for model_tool_id in sorted(available_model_tools):
            try:
                category_entry = self._registry.category_for_tool(model_tool_id)
            except Exception:
                continue
            primary = str(getattr(category_entry, "primary_category", "") or "").strip()
            secondary_raw = getattr(category_entry, "secondary_categories", [])
            if isinstance(secondary_raw, (list, tuple, set)):
                secondary = [
                    str(item).strip() for item in secondary_raw if str(item).strip()
                ]
            else:
                secondary = []
            if token == primary or token in secondary:
                matches.append(model_tool_id)
        return matches

    @staticmethod
    def _pick_category_default_model_tool(
        *, category: str, model_ids: list[str]
    ) -> str:
        if not model_ids:
            return ""

        preferred = _PREFERRED_MODEL_TOOLS_BY_CATEGORY.get(
            str(category or "").strip(), []
        )
        available = set(model_ids)
        for candidate in preferred:
            if candidate in available:
                return candidate

        return model_ids[0]

    def _canonical_model_tool_name(self, tool_name: str) -> str:
        token = str(tool_name or "").strip()
        if not token:
            return ""
        canonical = self._normalize_model_tool_token(token)
        if not canonical:
            return token
        if callable(getattr(self._registry, "provider_spec_for_name", None)):
            if self._registry.provider_spec_for_name(canonical) is not None:
                return canonical
        return canonical

    def _category_compat_model_tool_id(self, category: str) -> str:
        token = str(category or "").strip()
        canonical = self._normalize_model_tool_token(token)
        if not canonical or canonical not in _CANONICAL_CATEGORY_COMPAT_IDS:
            return ""
        runtime_tools = self._runtime_tools_for_category(token)
        if not runtime_tools:
            return ""
        return canonical

    def _coerce_runtime_alias_to_requested_category_tool(
        self,
        *,
        selected_tool: str,
        category: str,
    ) -> str:
        token = str(selected_tool or "").strip()
        if not token:
            return ""
        canonical = self._category_compat_model_tool_id(category)
        if not canonical:
            return ""
        if token == canonical:
            return canonical
        if token in self._runtime_tools_for_category(category):
            return canonical
        return ""

    def _runtime_tools_for_category(self, category: str) -> list[str]:
        category_token = str(category or "").strip()
        if not category_token:
            return []
        tools_by_category = getattr(self._registry, "tools_by_category", None)
        if not callable(tools_by_category):
            return []
        try:
            raw_tools = tools_by_category(category_token)
        except Exception:
            return []
        runtime_tools: list[str] = []
        seen: set[str] = set()
        for item in raw_tools or []:
            token = str(item or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            runtime_tools.append(token)
        return runtime_tools

    def _normalize_model_tool_token(self, token: str) -> str | None:
        global _REGISTRY_MANAGER_WARMED
        normalized = str(token or "").strip()
        if not normalized:
            return None
        canonical = _get_registry_manager().normalize_raw_name(normalized)
        if not canonical and not _REGISTRY_MANAGER_WARMED:
            try:
                from openminion.modules.tool.bootstrap import (
                    wire_default_tool_registry_manager,
                )

                wire_default_tool_registry_manager()
            except Exception:
                pass
            _REGISTRY_MANAGER_WARMED = True
            canonical = _get_registry_manager().normalize_raw_name(normalized)
        if canonical:
            return canonical
        return normalize_raw_model_tool_name(normalized)

    def _canonicalize_tool_chain(self, tool_names: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in tool_names:
            canonical = self._canonical_model_tool_name(str(item or "").strip())
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            out.append(canonical)
        return out

    def _apply_identity_tool_filter(
        self, tools: list[ProviderToolSpec], identity_tool_filter: dict[str, Any]
    ) -> _FilterOutcome:
        if not identity_tool_filter:
            return _FilterOutcome(specs=list(tools), unresolved_category_count=0)

        allowed_tools = identity_tool_filter.get("allowed_tools")
        blocked_patterns = identity_tool_filter.get("blocked_patterns", [])
        tool_use_type = str(identity_tool_filter.get("tool_use", "")).strip().lower()

        enforce_allowlist = tool_use_type in {"restricted", "read_only"}
        if allowed_tools and enforce_allowlist:
            tools = [tool for tool in tools if tool.name in allowed_tools]

        if blocked_patterns:
            filtered_tools = []
            for tool in tools:
                should_include = True
                for pattern in blocked_patterns:
                    if fnmatch.fnmatch(tool.name, pattern):
                        should_include = False
                        break
                if should_include:
                    filtered_tools.append(tool)
            tools = filtered_tools

        unresolved_category_count = 0
        if tool_use_type == "read_only":
            filtered_tools = []
            for tool in tools:
                write_exec = self._is_write_exec_tool(tool.name)
                if write_exec is True:
                    continue
                if write_exec is None:
                    unresolved_category_count += 1
                    _LOG.debug(
                        "tool selection read_only unresolved category lookup for %s",
                        tool.name,
                    )
                filtered_tools.append(tool)
            tools = filtered_tools

        return _FilterOutcome(
            specs=list(tools),
            unresolved_category_count=unresolved_category_count,
        )

    @staticmethod
    def _identity_filter_cache_key(
        tools: list[ProviderToolSpec],
        identity_tool_filter: dict[str, Any],
    ) -> str:
        tool_names = tuple(sorted(str(tool.name or "").strip() for tool in tools))
        return json.dumps(
            {
                "filter": identity_tool_filter,
                "tool_names": tool_names,
            },
            default=str,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _is_write_exec_tool(self, tool_name: str) -> bool | None:
        token = str(tool_name or "").strip()
        if not token:
            return None
        try:
            category_entry = self._registry.category_for_tool(token)
        except Exception:
            return None

        primary = str(getattr(category_entry, "primary_category", "") or "").strip()
        secondary_raw = getattr(category_entry, "secondary_categories", [])
        secondary: list[str] = []
        if isinstance(secondary_raw, (list, tuple, set)):
            secondary = [
                str(item).strip() for item in secondary_raw if str(item).strip()
            ]
        if not primary and not secondary:
            return None

        categories = [primary, *secondary]
        normalized_categories = {item for item in categories if item}
        if not normalized_categories:
            return None
        return bool(normalized_categories & _READ_ONLY_BLOCKED_CATEGORIES)

    def _full_catalog_selection(
        self, *, specs: Optional[List[ProviderToolSpec]] = None
    ) -> SelectionResult:
        """TSSR-03: typed-signal-absent fallback."""
        all_tools = list(specs) if specs is not None else self._registry_specs()
        if not all_tools:
            return SelectionResult(
                mode="typed",
                shortlist=[],
                stubs=[],
                full_schema_tools=[],
                category=None,
                binding_source=None,
                fallback_used=False,
                token_estimate=0,
                reason_codes=["no_tools"],
            )

        # Deterministic sort by tool name; no prose input.
        ordered = sorted(all_tools, key=lambda t: t.name)

        shortlist: List[str] = []
        stubs: List[ToolStub] = []
        excluded: List[str] = []
        total_tokens = 0
        max_tools = self._config.max_tools_per_turn
        token_budget = self._config.tool_prompt_token_budget

        for tool in ordered:
            if len(shortlist) >= max_tools:
                excluded.append(tool.name)
                continue
            stub = self._generate_stub(tool.name)
            if not stub:
                continue
            stub_tokens = self._estimate_stub_tokens(stub)
            if total_tokens + stub_tokens > token_budget:
                excluded.append(tool.name)
                continue
            shortlist.append(tool.name)
            stubs.append(stub)
            total_tokens += stub_tokens

        reason_codes = [
            "full_catalog",
            f"max_tools:{max_tools}",
            f"budget:{token_budget}",
        ]
        if excluded:
            reason_codes.append(f"truncated:{len(excluded)}")

        # typed telemetry — full catalog exposure + truncation.
        _LOG.info(
            "selection.full_catalog_exposed",
            extra={
                "event_type": "selection.full_catalog_exposed",
                "tool_count": len(shortlist),
                "token_estimate": total_tokens,
            },
        )
        if excluded:
            _LOG.info(
                "selection.catalog_truncated_by_budget",
                extra={
                    "event_type": "selection.catalog_truncated_by_budget",
                    "excluded_tool_count": len(excluded),
                    "token_budget": token_budget,
                    "sort_order": "alphabetical_by_name",
                },
            )

        return SelectionResult(
            mode="typed",
            shortlist=shortlist,
            stubs=stubs,
            full_schema_tools=[],
            category=None,
            binding_source=None,
            fallback_used=False,
            token_estimate=total_tokens,
            reason_codes=reason_codes,
        )

    def _generate_stub(self, tool_name: str) -> Optional[ToolStub]:
        spec = None
        if callable(getattr(self._registry, "provider_spec_for_name", None)):
            spec = self._registry.provider_spec_for_name(tool_name)
        if spec is None:
            tool = self._registry._tools.get(tool_name)
            if tool is None:
                return None
            try:
                spec = tool.provider_spec()
            except Exception:
                spec = ProviderToolSpec(
                    name=tool_name,
                    description=str(getattr(tool, "description", "") or ""),
                    parameters=dict(getattr(tool, "parameters", {}) or {}),
                )

        desc = str(getattr(spec, "description", "") or "")
        params = self._manager_schema_for(str(getattr(spec, "name", "") or tool_name))
        if not params:
            params = dict(getattr(spec, "parameters", {}) or {})

        desc_short = (
            desc[:_TOOL_STUB_DESCRIPTION_MAX_CHARS]
            if len(desc) > _TOOL_STUB_DESCRIPTION_MAX_CHARS
            else desc
        )

        required_args: List[str] = []
        if isinstance(params, dict):
            required = params.get("required", [])
            if isinstance(required, list):
                required_args = [str(r) for r in required[:6]]
            props = params.get("properties", {})
            if isinstance(props, dict) and not required_args:
                for prop_name, prop_def in props.items():
                    if isinstance(prop_def, dict) and prop_def.get("required"):
                        required_args.append(str(prop_name))
                        if len(required_args) >= 6:
                            break

        example: Dict[str, Any] = {}
        for arg in required_args[:3]:
            example[arg] = f"<{arg}>"

        return ToolStub(
            name=tool_name,
            description_short=desc_short,
            required_args=required_args,
            example_minimal=example,
        )

    def _estimate_stub_tokens(self, stub: ToolStub) -> int:
        text = json.dumps(
            {
                "name": stub.name,
                "description_short": stub.description_short,
                "required_args": stub.required_args,
            }
        )
        return max(1, len(text) // 4)

    def should_expand_schema(
        self,
        tool_name: str,
        validation_error: Optional[ValidationError] = None,
    ) -> bool:
        if self._schema_exposure == SchemaExposure.FULL:
            return False

        if validation_error is None:
            return False

        if self._config.validation_retry_max <= 0:
            return False

        return True

    def get_full_schema(self, tool_name: str) -> Optional[ProviderToolSpec]:
        spec: ProviderToolSpec | None = None
        if callable(getattr(self._registry, "provider_spec_for_name", None)):
            spec = self._registry.provider_spec_for_name(tool_name)
        else:
            tool = self._registry._tools.get(tool_name)
            if tool is None:
                return None
            try:
                spec = tool.provider_spec()
            except Exception:
                spec = None
        if spec is None:
            return None
        schema = self._manager_schema_for(str(getattr(spec, "name", "") or tool_name))
        if not schema:
            schema = dict(getattr(spec, "parameters", {}) or {})
        return ProviderToolSpec(
            name=str(getattr(spec, "name", "") or tool_name),
            description=str(getattr(spec, "description", "") or ""),
            parameters=schema,
        )

    def create_shortlist_plan(
        self, query: str, intent_categories: Optional[List[str]] = None
    ) -> ShortlistPlan:
        result = self.select_tools(query, intent_categories=intent_categories)
        return ShortlistPlan(
            query=query,
            mode=result.mode,
            selected_categories=[result.category] if result.category else [],
            selected_tools=result.shortlist,
            token_budget=self._config.tool_prompt_token_budget,
            estimated_tokens=result.token_estimate,
            reason_codes=result.reason_codes,
            fallback_chain=self.get_fallback_tools_for_category(result.category or ""),
        )


class ValidationRetryManager:
    def __init__(
        self, config: ToolSelectionConfig, service: ToolSelectionService
    ) -> None:
        self._config = config
        self._service = service
        self._retry_counts: Dict[str, int] = {}

    def should_retry(
        self,
        tool_name: str,
        error: ValidationError,
    ) -> bool:
        if self._config.validation_retry_max <= 0:
            return False

        current_count = self._retry_counts.get(tool_name, 0)
        if current_count >= self._config.validation_retry_max:
            return False

        return True

    def record_retry(self, tool_name: str) -> None:
        self._retry_counts[tool_name] = self._retry_counts.get(tool_name, 0) + 1

    def get_expanded_schema(
        self,
        tool_name: str,
    ) -> Optional[ProviderToolSpec]:
        return self._service.get_full_schema(tool_name)

    def get_retry_count(self, tool_name: str) -> int:
        return self._retry_counts.get(tool_name, 0)


def create_tool_selection_service(
    config: ToolSelectionConfig, registry: ToolRegistry
) -> ToolSelectionService:
    return ToolSelectionService(config=config, registry=registry)
