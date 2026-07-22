from __future__ import annotations

from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
)
from collections.abc import Callable, Iterable, Mapping, Sequence

from openminion.modules.tool.contracts import ProviderToolCall, ProviderToolSpec
from openminion.modules.tool.executor import (  # noqa: F401  (re-exported)
    ToolExecutionBatch,
    dependency_error_result as _executor_dependency_error_result,
    execute_calls as _executor_execute_calls,
    execute_calls_with_dependencies as _executor_execute_calls_with_dependencies,
    execute_single_call as _executor_execute_single_call,
    is_retry_eligible_for_fallback as _executor_is_retry_eligible_for_fallback,
    normalize_tool_call as _executor_normalize_tool_call,
)
from openminion.modules.tool.runtime.dispatch import (
    resolve_binding_for_call,
)
from .catalog import (  # noqa: F401  (re-exported)
    ToolCategoryEntry,
    ToolPolicyProfile,
    ToolSpec,
    add_tool_spec as _catalog_add_tool_spec,
    all_categories as _catalog_all_categories,
    category_for_tool as _catalog_category_for_tool,
    index_tool_category as _catalog_index_tool_category,
    infer_categories_from_index as _catalog_infer_categories_from_index,
    list_by_capability as _catalog_list_by_capability,
    register_tool as _catalog_register_tool,
    unregister_tool as _catalog_unregister_tool,
    tools_by_category as _catalog_tools_by_category,
)
from openminion.modules.tool.runtime.registry_toolspec import (
    execute_tool_spec_call as execute_tool_spec_call_registry_toolspec_runtime,
    invoke_tool_spec_handler as invoke_tool_spec_handler_registry_toolspec_runtime,
    resolve_run_root as resolve_run_root_registry_toolspec_runtime,
    resolve_tool_scope as resolve_tool_scope_registry_toolspec_runtime,
    resolve_workspace as resolve_workspace_registry_toolspec_runtime,
)
from openminion.modules.tool.base import (
    Tool,
    ToolCategoryInfo,
    ToolExecutionContext,
    ToolExecutionResult,
)
from openminion.modules.tool.errors import ToolRuntimeError
import builtins

if TYPE_CHECKING:
    from openminion.modules.tool.exposure import ToolExposureService
    from openminion.tools.mcp.interfaces import MCPFleetHandle
    from openminion.modules.tool.runtime import RuntimeContext

_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK_ENV = (
    "OPENMINION_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK"
)

Handler = Callable[[dict[str, Any], "RuntimeContext"], dict[str, Any]]
Scope = Literal["READ_ONLY", "WRITE_SAFE", "POWER_USER", "UI_AUTOMATION"]


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        from openminion.modules.tool.exposure import ToolExposureService

        self._tools: dict[str, Any] = {}
        self._category_index: dict[str, set[str]] = {}
        self._sidecar_autostart: Callable[..., dict[str, Any]] | None = None
        self.mcp_manager: MCPFleetHandle | None = None
        self._exposure_service = ToolExposureService()
        for tool in tools or []:
            self.register(tool)

    @property
    def exposure_service(self) -> "ToolExposureService":
        return self._exposure_service

    def register(self, tool: Any) -> None:
        _catalog_register_tool(self, tool)

    def bind_sidecar_autostart(self, callback: Callable[..., dict[str, Any]]) -> None:
        self._sidecar_autostart = callback

    def ensure_sidecar_autostart(self, **kwargs: Any) -> dict[str, Any]:
        if self._sidecar_autostart is None:
            raise ToolRuntimeError(
                "DEPENDENCY_MISSING",
                "sidecar autostart is unavailable in this runtime",
            )
        return self._sidecar_autostart(**kwargs)

    def unregister(self, tool_name: str) -> None:
        _catalog_unregister_tool(self, tool_name)

    def _index_tool_category(self, tool_name: str, tool: Tool) -> None:
        _catalog_index_tool_category(self, tool_name, tool)

    def tools_by_category(self, category: str) -> builtins.list[str]:
        return _catalog_tools_by_category(self, category)

    def category_for_tool(self, tool_name: str) -> ToolCategoryEntry:
        return _catalog_category_for_tool(self, tool_name)

    def all_categories(self) -> builtins.list[str]:
        return _catalog_all_categories(self)

    def add(self, spec: Any) -> None:
        """Shim for openminion-tool plugin compatibility."""
        _catalog_add_tool_spec(self, spec)

    def _infer_categories_from_index(self, tool_name: str) -> ToolCategoryInfo:
        return _catalog_infer_categories_from_index(self, tool_name)

    def get(self, name: str) -> Any:
        """Shim for openminion-tool plugin compatibility."""
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(name)
        return tool

    def list(self) -> dict[str, Any]:
        """Shim for openminion-tool plugin compatibility."""
        return dict(self._tools)

    def list_by_capability(self, capability: str) -> builtins.list[ToolSpec]:
        """Shim for openminion-tool plugin compatibility."""
        return _catalog_list_by_capability(self, capability)

    def provider_specs(self) -> list[ProviderToolSpec]:
        from .exposure import provider_specs

        return provider_specs(self)

    def model_provider_specs(self) -> list[ProviderToolSpec]:
        from .exposure import model_provider_specs

        return model_provider_specs(self)

    def model_to_runtime_binding_map(self) -> dict[str, str]:
        from .exposure import (
            model_to_runtime_binding_map,
        )

        return model_to_runtime_binding_map(self)

    def model_to_runtime_tool_map(self) -> dict[str, str]:
        from .exposure import (
            model_to_runtime_tool_map,
        )

        return model_to_runtime_tool_map(self)

    def model_runtime_dispatch_map(self) -> dict[str, dict[str, Any]]:
        from .exposure import (
            model_runtime_dispatch_map,
        )

        return model_runtime_dispatch_map(self)

    def registration_debug_snapshot(self) -> dict[str, Any]:
        from .exposure import (
            registration_debug_snapshot,
        )

        return registration_debug_snapshot(self)

    def provider_spec_for_name(self, name: str) -> ProviderToolSpec | None:
        from .exposure import provider_spec_for_name

        return provider_spec_for_name(self, name)

    def _provider_spec_for_runtime_name(
        self, tool_name: str
    ) -> ProviderToolSpec | None:
        from .exposure import (
            provider_spec_for_runtime_name,
        )

        return provider_spec_for_runtime_name(self, tool_name)

    def policy_for(self, tool_name: str) -> ToolPolicyProfile:
        key = str(tool_name or "").strip()
        tool = self._tools.get(key)
        if tool is None:
            manager = self._binding_manager()
            model_tool_id = manager.normalize_raw_name(key)
            if model_tool_id:
                runtime_map = manager.model_to_runtime_tool_map(set(self._tools.keys()))
                mapped_runtime_tool = runtime_map.get(model_tool_id)
                if mapped_runtime_tool:
                    tool = self._tools.get(mapped_runtime_tool)
                    if tool is not None:
                        key = mapped_runtime_tool
        if tool is None:
            resolution = resolve_binding_for_call(
                raw_tool_name=key,
                available_tool_names=tuple(self._tools.keys()),
            )
            if resolution is not None and resolution.runtime_tool_name:
                tool = self._tools.get(resolution.runtime_tool_name)
                if tool is not None:
                    key = resolution.runtime_tool_name
        if tool is None:
            return ToolPolicyProfile(
                tool_name=key or "unknown",
                required_scopes_all=frozenset({"tool.execute"}),
                risk="medium",
                budget_cost=1,
            )
        if isinstance(tool, ToolSpec):
            min_scope = (
                str(getattr(tool, "min_scope", "READ_ONLY") or "READ_ONLY")
                .strip()
                .upper()
            )
            required_scopes = {"tool.execute"}
            if min_scope in {"POWER_USER", "UI_AUTOMATION"}:
                required_scopes.add("tool.execute.elevated")
            risk = "high" if bool(getattr(tool, "dangerous", False)) else "medium"
            budget_cost = 2 if risk == "high" else 1
            return ToolPolicyProfile(
                tool_name=str(getattr(tool, "name", key) or key).strip()
                or key
                or "unknown",
                required_scopes_all=frozenset(required_scopes),
                risk=risk,
                budget_cost=budget_cost,
            )
        policy = tool.execution_policy()
        try:
            budget_cost = max(1, int(policy.budget_cost))
        except (TypeError, ValueError):
            budget_cost = 1
        return ToolPolicyProfile(
            tool_name=str(tool.name or key).strip() or key or "unknown",
            required_scopes_all=frozenset(policy.required_scopes_all),
            risk=str(policy.risk or "medium").strip().lower() or "medium",
            budget_cost=budget_cost,
        )

    def _binding_manager(self):
        # Resolver manager is the authoritative, bootstrap-wired source.
        from openminion.modules.tool.runtime.dispatch import get_registry_manager

        manager = get_registry_manager()
        if manager.model_provider_specs(set(self._tools.keys())):
            return manager

        # If resolver manager is still empty, bootstrap default manifests once.
        try:
            from openminion.modules.tool.bootstrap import (
                wire_default_tool_registry_manager,
            )

            wire_default_tool_registry_manager()
            manager = get_registry_manager()
        except Exception:
            pass
        return manager

    def execute_calls(
        self,
        tool_calls: Sequence[ProviderToolCall],
        *,
        context: ToolExecutionContext,
    ) -> ToolExecutionBatch:
        return _executor_execute_calls(self, tool_calls, context=context)

    def _execute_calls_with_dependencies(
        self,
        *,
        calls: Sequence[ProviderToolCall],
        context: ToolExecutionContext,
        available_tool_names: tuple[str, ...],
        runtime_binding_policies: Any,
    ) -> list[ToolExecutionResult]:
        return _executor_execute_calls_with_dependencies(
            self,
            calls=calls,
            context=context,
            available_tool_names=available_tool_names,
            runtime_binding_policies=runtime_binding_policies,
        )

    def _execute_single_call(
        self,
        *,
        call: ProviderToolCall,
        context: ToolExecutionContext,
        available_tool_names: tuple[str, ...],
        runtime_binding_policies: Any,
    ) -> ToolExecutionResult:
        return _executor_execute_single_call(
            self,
            call=call,
            context=context,
            available_tool_names=available_tool_names,
            runtime_binding_policies=runtime_binding_policies,
        )

    @staticmethod
    def _dependency_error_result(
        *,
        call: ProviderToolCall,
        error_code: str,
        reason_code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> ToolExecutionResult:
        return _executor_dependency_error_result(
            call=call,
            error_code=error_code,
            reason_code=reason_code,
            message=message,
            details=details,
        )

    @staticmethod
    def _normalize_tool_call(call: ProviderToolCall) -> ProviderToolCall:
        return _executor_normalize_tool_call(call)

    @staticmethod
    def _is_retry_eligible_for_fallback(
        result: ToolExecutionResult,
        *,
        runtime_binding_policies: Any,
    ) -> bool:
        return _executor_is_retry_eligible_for_fallback(
            result, runtime_binding_policies=runtime_binding_policies
        )

    def _execute_tool_spec_call(
        self,
        *,
        tool: Any,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        return execute_tool_spec_call_registry_toolspec_runtime(
            tool=tool,
            arguments=arguments,
            context=context,
        )

    @staticmethod
    def _invoke_tool_spec_handler(
        *,
        handler: Any,
        arguments: Mapping[str, Any],
        runtime_ctx: Any,
    ) -> Any:
        return invoke_tool_spec_handler_registry_toolspec_runtime(
            handler=handler,
            arguments=arguments,
            runtime_ctx=runtime_ctx,
        )

    @staticmethod
    def _resolve_workspace(*, context: ToolExecutionContext) -> Path:
        return resolve_workspace_registry_toolspec_runtime(context=context)

    @staticmethod
    def _resolve_run_root(*, workspace: Path, context: ToolExecutionContext) -> Path:
        return resolve_run_root_registry_toolspec_runtime(
            workspace=workspace, context=context
        )

    @staticmethod
    def _resolve_tool_scope(*, tool: Any) -> str:
        return resolve_tool_scope_registry_toolspec_runtime(tool=tool)
