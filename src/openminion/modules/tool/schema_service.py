import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from .config import (
    PROMPT_TOOL_ARG_DESC_MAX_CHARS as _PROMPT_TOOL_ARG_DESC_MAX_CHARS,
    PROMPT_TOOL_DESC_MAX_CHARS as _PROMPT_TOOL_DESC_MAX_CHARS,
    PROMPT_TOOL_REQUIRED_ARG_LIMIT as _PROMPT_TOOL_REQUIRED_ARG_LIMIT,
    PROMPT_TOOL_STUB_LIMIT as _PROMPT_TOOL_STUB_LIMIT,
)
from .exposure import get_model_exposure_specs

# Prompt stubs are compact schema summaries for structured phases only. They
_STRUCTURED_PROMPT_TOOL_STUB_PURPOSES = frozenset(
    {"decide", "plan", "judge", "validate"}
)
_INCLUDE_EXPERIMENTAL_AUTHORED_ENV = "OPENMINION_INCLUDE_EXPERIMENTAL_AUTHORED_TOOLS"


@dataclass(frozen=True)
class ToolExposureBundle:
    """Unified tool injection payload for prompt + LLM call assembly."""

    execution_tools: tuple[dict[str, Any], ...]
    system_tools: tuple[dict[str, Any], ...]
    prompt_tool_stubs: tuple[dict[str, Any], ...]


CallerContext = Literal["context_build", "llm_request"]


class ToolSchemaService:
    """Single source for tool exposure shaping across brain/context."""

    @staticmethod
    def parse_prompt_schema_mode(value: Any, *, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def prompt_schemas_enabled(
        self,
        *,
        explicit: Any = None,
        env_var: str = "OPENMINION_PROMPT_TOOL_SCHEMAS",
        default: bool = False,
        env: EnvironmentConfig | Mapping[str, object] | None = None,
    ) -> bool:
        if explicit is not None:
            return self.parse_prompt_schema_mode(explicit, default=default)
        value = resolve_environment_config(env=env).get(env_var, "")
        return self.parse_prompt_schema_mode(value, default=default)

    def collect_execution_tool_schemas(
        self,
        *,
        registry: Any,
        normalize_name: Callable[[str], str | None] | None = None,
    ) -> list[dict[str, Any]]:
        if registry is None:
            return []

        tool_schemas: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        manager: Any | None = None
        binding_manager_fn = getattr(registry, "_binding_manager", None)
        if callable(binding_manager_fn):
            try:
                manager = binding_manager_fn()
            except Exception:
                manager = None

        for spec in get_model_exposure_specs(registry):
            name = str(getattr(spec, "name", "")).strip()
            if not name or name in seen_names:
                continue
            description = str(getattr(spec, "description", "") or "").strip()
            parameters: dict[str, Any] = {}
            if manager is not None and callable(getattr(manager, "schema_for", None)):
                try:
                    parameters = dict(manager.schema_for(name) or {})
                except Exception:
                    parameters = {}
            if (
                isinstance(parameters, dict)
                and parameters.get("type") == "object"
                and not isinstance(parameters.get("properties"), dict)
                and parameters.get("additionalProperties") is True
            ):
                parameters = {}
            if (
                isinstance(parameters, dict)
                and parameters.get("type") == "object"
                and isinstance(parameters.get("properties"), dict)
                and not parameters.get("properties")
                and parameters.get("additionalProperties") is True
            ):
                parameters = {}
            if not parameters:
                parameters = (
                    dict(getattr(spec, "parameters", {}) or {})
                    if isinstance(getattr(spec, "parameters", {}), dict)
                    else {}
                )
            tool_schemas.append(
                {
                    "name": name,
                    "description": description or f"Tool `{name}`",
                    "parameters": parameters,
                    **self._tool_metadata(registry=registry, tool_name=name),
                    "tool_lane": "execution",
                    "dispatchable": True,
                }
            )
            seen_names.add(name)

        raw_tools_map = getattr(registry, "_tools", None)
        if isinstance(raw_tools_map, Mapping):
            for raw_name, raw_tool in raw_tools_map.items():
                if not bool(getattr(raw_tool, "prompt_visible_runtime_name", False)):
                    continue
                name = str(raw_name or "").strip()
                if not name or name in seen_names:
                    continue
                tags = tuple(
                    str(tag or "").strip().lower()
                    for tag in getattr(raw_tool, "tags", ()) or ()
                )
                if "origin:authored" in tags and "experimental" in tags:
                    include_experimental = self.parse_prompt_schema_mode(
                        resolve_environment_config().get(
                            _INCLUDE_EXPERIMENTAL_AUTHORED_ENV,
                            "",
                        ),
                        default=False,
                    )
                    if not include_experimental:
                        continue
                description = self._tool_description(tool=raw_tool)
                parameters = self._tool_parameters(tool=raw_tool)
                tool_schemas.append(
                    {
                        "name": name,
                        "description": description or f"Tool `{name}`",
                        "parameters": parameters,
                        **self._tool_metadata(registry=registry, tool_name=name),
                        "tool_lane": "execution",
                        "dispatchable": True,
                    }
                )
                seen_names.add(name)

        if not tool_schemas:
            raw_tools: list[Any] = []
            tools_dict = getattr(registry, "_tools", None)
            if isinstance(tools_dict, dict):
                raw_tools = list(tools_dict.values())
            elif isinstance(tools_dict, (list, tuple)):
                raw_tools = list(tools_dict)
            else:
                alt_tools = getattr(registry, "tools", None)
                if isinstance(alt_tools, dict):
                    raw_tools = list(alt_tools.values())
                elif isinstance(alt_tools, (list, tuple)):
                    raw_tools = list(alt_tools)
                elif callable(getattr(registry, "list", None)):
                    try:
                        listed = registry.list()
                        if isinstance(listed, dict):
                            raw_tools = list(listed.values())
                        elif isinstance(listed, (list, tuple)):
                            raw_tools = list(listed)
                    except Exception:
                        raw_tools = []

            for tool in raw_tools:
                raw_name = str(getattr(tool, "name", "")).strip()
                name = raw_name
                if callable(normalize_name):
                    normalized = normalize_name(raw_name)
                    if normalized:
                        name = normalized
                if not name or name in seen_names:
                    continue
                description = self._tool_description(tool=tool)
                parameters = self._tool_parameters(tool=tool)
                tool_schemas.append(
                    {
                        "name": name,
                        "description": description or f"Tool `{name}`",
                        "parameters": parameters,
                        **self._tool_metadata(registry=registry, tool_name=name),
                        "tool_lane": "execution",
                        "dispatchable": True,
                    }
                )
                seen_names.add(name)

        tool_schemas.sort(key=lambda entry: str(entry.get("name", "")))
        return tool_schemas

    def build_prompt_tool_schemas(
        self,
        *,
        query: str,
        tool_schemas: list[dict[str, Any]] | None,
        stub_limit: int = _PROMPT_TOOL_STUB_LIMIT,
    ) -> list[dict[str, Any]]:
        raw_tools: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for entry in tool_schemas or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            raw_tools.append(entry)
        if not raw_tools:
            return []

        exact_names = self._exact_tool_names_mentioned(
            query=query,
            tool_names=[str(item.get("name", "") or "") for item in raw_tools],
        )
        selected: list[dict[str, Any]] = []
        selected_names: set[str] = set()

        for item in raw_tools:
            name = str(item.get("name", "") or "").strip()
            if name in exact_names and name not in selected_names:
                selected.append(item)
                selected_names.add(name)

        for item in raw_tools:
            if len(selected) >= stub_limit:
                break
            name = str(item.get("name", "") or "").strip()
            if name in selected_names:
                continue
            selected.append(item)
            selected_names.add(name)

        return [self.tool_stub(item) for item in selected]

    @staticmethod
    def _exact_tool_names_mentioned(*, query: str, tool_names: list[str]) -> set[str]:
        """Return registered tool IDs named exactly in user text.

        This is not semantic ranking: only canonical registered names survive,
        and the original registry order still fills the remaining prompt slots.
        """
        text = str(query or "")
        if not text:
            return set()
        mentioned: set[str] = set()
        for raw_name in tool_names:
            name = str(raw_name or "").strip()
            if not name:
                continue
            pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(name)}(?![A-Za-z0-9_.-])"
            if re.search(pattern, text):
                mentioned.add(name)
        return mentioned

    def build_system_tools(
        self,
        *,
        structured_schema: Any,
    ) -> list[dict[str, Any]]:
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        if structured_schema is not None and callable(
            getattr(structured_schema, "flat_json_schema", None)
        ):
            try:
                maybe = structured_schema.flat_json_schema()
                if isinstance(maybe, dict):
                    parameters = dict(maybe)
            except Exception:
                pass
        elif structured_schema is not None and callable(
            getattr(structured_schema, "json_schema", None)
        ):
            try:
                maybe = structured_schema.json_schema()
                if isinstance(maybe, dict):
                    parameters = dict(maybe)
            except Exception:
                pass
        elif structured_schema is not None and callable(
            getattr(structured_schema, "model_json_schema", None)
        ):
            try:
                maybe = structured_schema.model_json_schema()
                if isinstance(maybe, dict):
                    parameters = dict(maybe)
            except Exception:
                pass
        return [
            {
                "name": "submit_output",
                "description": "Submit the structured output",
                "parameters": parameters,
                "tool_lane": "system",
                "dispatchable": False,
            }
        ]

    def build_exposure_bundle(
        self,
        *,
        query: str,
        execution_tools: list[dict[str, Any]] | None,
        structured_schema: Any,
    ) -> ToolExposureBundle:
        runtime = list(execution_tools or [])
        system = self.build_system_tools(structured_schema=structured_schema)
        stubs = self.build_prompt_tool_schemas(query=query, tool_schemas=runtime)
        return ToolExposureBundle(
            execution_tools=tuple(runtime),
            system_tools=tuple(system),
            prompt_tool_stubs=tuple(stubs),
        )

    def get_tools_for_purpose(
        self,
        *,
        purpose: str,
        query: str,
        caller_context: CallerContext,
        execution_tools: list[dict[str, Any]] | None,
        structured_schema: Any = None,
        prompt_schemas_enabled: Any = None,
    ) -> ToolExposureBundle:
        purpose_key = str(purpose or "").strip().lower()
        caller_context_key = str(caller_context or "").strip().lower()
        if caller_context_key not in {"context_build", "llm_request"}:
            raise ValueError(
                f"unsupported caller_context: {caller_context!r}"
            )  # allow-bare-raise: internal invariant — caller_context is a module-internal Literal-style value
        include_prompt_stubs = (
            caller_context_key == "context_build"
            and self.prompt_schemas_enabled(
                explicit=prompt_schemas_enabled,
                default=False,
            )
        )
        bundle = self.build_exposure_bundle(
            query=query,
            execution_tools=execution_tools,
            structured_schema=structured_schema,
        )
        if purpose_key not in {"decide", "plan", "chat", "judge", "validate"}:
            include_prompt_stubs = False
        elif (
            caller_context_key == "context_build"
            and purpose_key in _STRUCTURED_PROMPT_TOOL_STUB_PURPOSES
            and bundle.execution_tools
        ):
            # Structured phases remain schema-only at the provider layer, so they
            include_prompt_stubs = True
        if include_prompt_stubs:
            return bundle
        return ToolExposureBundle(
            execution_tools=bundle.execution_tools,
            system_tools=bundle.system_tools,
            prompt_tool_stubs=tuple(),
        )

    def _tool_metadata(self, *, registry: Any, tool_name: str) -> dict[str, Any]:
        tools = getattr(registry, "_tools", None)
        tool = tools.get(tool_name) if isinstance(tools, dict) else None
        capability_tags: list[str] = []
        feasibility_descriptors: list[str] = []
        metadata_warnings: list[str] = []
        risk_level = "unknown"
        agent_visibility = "local"
        auth_status = "unknown"
        runtime_status = "available"
        rate_limit_state = "ok"
        config_status = "configured"

        if tool is not None:
            category_info = getattr(tool, "category_info", None)
            if callable(category_info):
                try:
                    category = category_info()
                except Exception:
                    category = None
            else:
                category = None
            if category is not None:
                primary = str(getattr(category, "primary_category", "") or "").strip()
                secondary = [
                    str(item).strip()
                    for item in (getattr(category, "secondary_categories", ()) or ())
                    if str(item).strip()
                ]
                capability_tags = [item for item in [primary, *secondary] if item]
            policy = getattr(tool, "execution_policy", None)
            if callable(policy):
                try:
                    policy_value = policy()
                except Exception:
                    policy_value = None
            else:
                policy_value = None
            if policy_value is not None:
                risk_level = (
                    str(getattr(policy_value, "risk", "") or "").strip() or "unknown"
                )
            if capability_tags:
                feasibility_descriptors.extend(capability_tags)
            if risk_level and risk_level != "unknown":
                feasibility_descriptors.append(f"risk:{risk_level}")

        if not capability_tags:
            metadata_warnings.append("missing_capability_tags")
        if not feasibility_descriptors:
            metadata_warnings.append("missing_feasibility_descriptors")

        return {
            "capability_tags": capability_tags,
            "feasibility_descriptors": feasibility_descriptors,
            "agent_visibility": agent_visibility,
            "auth_status": auth_status,
            "runtime_status": runtime_status,
            "rate_limit_state": rate_limit_state,
            "config_status": config_status,
            "risk_level": risk_level,
            "metadata_complete": not metadata_warnings,
            "metadata_warnings": metadata_warnings,
        }

    def trim_text(self, text: str, max_chars: int) -> str:
        value = " ".join(str(text or "").split()).strip()
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 3].rstrip() + "..."

    def tool_stub(self, item: dict[str, Any]) -> dict[str, Any]:
        name = str(item.get("name", "")).strip()
        description = self.trim_text(
            str(item.get("description", "")).strip(),
            _PROMPT_TOOL_DESC_MAX_CHARS,
        )

        parameters = item.get("parameters", {})
        if not isinstance(parameters, dict):
            parameters = {}

        required_raw = parameters.get("required")
        required = [
            str(key).strip()
            for key in (required_raw if isinstance(required_raw, list) else [])
            if str(key).strip()
        ][:_PROMPT_TOOL_REQUIRED_ARG_LIMIT]

        properties_raw = parameters.get("properties")
        properties = properties_raw if isinstance(properties_raw, dict) else {}

        display_keys = list(required)
        if not display_keys and properties:
            for key in properties:
                display_keys.append(str(key))
                if len(display_keys) >= _PROMPT_TOOL_REQUIRED_ARG_LIMIT:
                    break

        stub_props: dict[str, Any] = {}
        for key in display_keys:
            raw_prop = properties.get(key)
            if not isinstance(raw_prop, dict):
                stub_props[key] = {"type": "string"}
                continue
            prop_type = str(raw_prop.get("type", "string")).strip() or "string"
            prop_desc = self.trim_text(
                str(raw_prop.get("description", "")).strip(),
                _PROMPT_TOOL_ARG_DESC_MAX_CHARS,
            )
            prop_schema: dict[str, Any] = {"type": prop_type}
            enum_values = raw_prop.get("enum")
            if isinstance(enum_values, list) and enum_values and len(enum_values) <= 8:
                prop_schema["enum"] = enum_values
            if prop_desc:
                prop_schema["description"] = prop_desc
            stub_props[key] = prop_schema

        stub_parameters: dict[str, Any] = {
            "type": "object",
            "properties": stub_props,
        }
        if required:
            stub_parameters["required"] = required
        additional = parameters.get("additionalProperties")
        if isinstance(additional, bool):
            stub_parameters["additionalProperties"] = additional

        return {
            "name": name,
            "description": description,
            "parameters": stub_parameters,
            "capability_tags": list(item.get("capability_tags", []) or []),
            "feasibility_descriptors": list(
                item.get("feasibility_descriptors", []) or []
            ),
            "agent_visibility": str(item.get("agent_visibility", "") or "").strip(),
            "metadata_complete": bool(item.get("metadata_complete", False)),
            "metadata_warnings": list(item.get("metadata_warnings", []) or []),
        }

    def tool_description(self, *, tool: Any) -> str:
        return self._tool_description(tool=tool)

    def tool_parameters(self, *, tool: Any) -> dict[str, Any]:
        return self._tool_parameters(tool=tool)

    def _tool_description(self, *, tool: Any) -> str:
        description = str(getattr(tool, "description", "") or "").strip()
        if description:
            return description
        provider_spec_factory = getattr(tool, "provider_spec", None)
        if callable(provider_spec_factory):
            try:
                provider_spec = provider_spec_factory()
            except Exception:
                provider_spec = None
            if provider_spec is not None:
                description = str(
                    getattr(provider_spec, "description", "") or ""
                ).strip()
                if description:
                    return description
        return ""

    def _tool_parameters(self, *, tool: Any) -> dict[str, Any]:
        parameters = getattr(tool, "parameters", None)
        if isinstance(parameters, dict):
            return dict(parameters)

        input_schema = getattr(tool, "input_schema", None)
        if isinstance(input_schema, dict):
            return dict(input_schema)

        args_model = getattr(tool, "args_model", None)
        if args_model is not None and callable(
            getattr(args_model, "model_json_schema", None)
        ):
            try:
                schema = args_model.model_json_schema()
                if isinstance(schema, dict):
                    return dict(schema)
            except Exception:
                pass
        return {}
