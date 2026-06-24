from dataclasses import dataclass
from typing import Any, Mapping

from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.llm.providers.base import ProviderToolSpec
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolExecutionBatch

from .ports import TurnFlowServicePort
from .state import TurnRuntimeContext


@dataclass(slots=True)
class AgentServiceTurnFlowAdapter:
    _service: Any

    @property
    def provider(self) -> Any:
        return getattr(self._service, "_provider", None)

    @property
    def logger(self) -> Any:
        return getattr(self._service, "_logger", None)

    @property
    def home_root(self) -> Any:
        return getattr(self._service, "_home_root", None)

    @property
    def identity_agent_id(self) -> str:
        return str(getattr(self._service, "_identity_agent_id", "") or "")

    @property
    def config(self) -> Any:
        return getattr(self._service, "_config", None)

    @property
    def tools(self) -> Any | None:
        return getattr(self._service, "_tools", None)

    @property
    def tool_selection(self) -> Any | None:
        return getattr(self._service, "_tool_selection", None)

    @property
    def identity_tool_filter(self) -> Any:
        return getattr(self._service, "_identity_tool_filter", None)

    @property
    def security_policy(self) -> Any | None:
        return getattr(self._service, "_security_policy", None)

    @property
    def self_improvement(self) -> Any | None:
        return getattr(self._service, "_self_improvement", None)

    async def generate_normalized(self, request: ProviderRequest) -> ProviderResponse:
        return await self._service._generate_normalized(request)

    def get_spec_for_tool(self, tool_name: str) -> ProviderToolSpec | None:
        try:
            return self._service._get_spec_for_tool(tool_name)
        except RuntimeError:
            tools = getattr(self._service, "_tools", None)
            if tools is None:
                return None
            try:
                return tools.provider_spec_for_name(tool_name)
            except Exception:
                return None

    def build_required_tool_retry_prompt(
        self,
        *,
        user_message: str,
        tool_name: str,
        spec: ProviderToolSpec | None,
    ) -> str:
        return self._service._build_required_tool_retry_prompt(
            user_message=user_message,
            tool_name=tool_name,
            spec=spec,
        )

    def normalize_required_tool_arguments(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._service._normalize_required_tool_arguments(
            tool_name=tool_name,
            arguments=dict(arguments),
        )

    def sanitize_arguments_for_spec(
        self,
        *,
        arguments: Mapping[str, Any],
        spec: ProviderToolSpec | None,
    ) -> dict[str, Any]:
        return self._service._sanitize_arguments_for_spec(
            arguments=dict(arguments),
            spec=spec,
        )

    def build_direct_fallback_arguments(
        self,
        *,
        tool_name: str,
        spec: ProviderToolSpec | None,
        inbound: Any,
    ) -> dict[str, Any] | None:
        return self._service._build_direct_fallback_arguments(
            tool_name=tool_name,
            spec=spec,
            inbound=inbound,
        )

    def execute_direct_tool_fallback(
        self,
        *,
        tool_name: str,
        spec: ProviderToolSpec | None,
        inbound: Any,
    ) -> ToolExecutionBatch | None:
        return self._service._execute_direct_tool_fallback(
            tool_name=tool_name,
            spec=spec,
            inbound=inbound,
        )

    def execute_single_tool_call(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
        source: str,
    ) -> ToolExecutionBatch:
        return self._service._execute_single_tool_call(
            tool_name=tool_name,
            arguments=dict(arguments),
            context=context,
            source=source,
        )

    def fallback_eligibility_reason(self, result: ToolExecutionResult) -> str | None:
        return self._service._fallback_eligibility_reason(result)

    def empty_tool_resolution_metadata(self) -> dict[str, str]:
        return self._service._empty_tool_resolution_metadata()

    def augment_browser_fallback_chain(
        self,
        *,
        fallback_chain: list[str],
    ) -> list[str]:
        return self._service._augment_browser_fallback_chain(
            fallback_chain=fallback_chain
        )


@dataclass(slots=True)
class TurnExecutorComponents:
    service_port: TurnFlowServicePort
    runtime_ops: Any
    required_lane: Any
    unforced_lane: Any


class TurnFlowBuilder:
    @staticmethod
    def build_service_port(service: Any) -> TurnFlowServicePort:
        return AgentServiceTurnFlowAdapter(_service=service)

    @staticmethod
    def build_turn_executor_components(
        *,
        service: Any | None = None,
        service_port: TurnFlowServicePort | None = None,
        runtime: TurnRuntimeContext,
    ) -> TurnExecutorComponents:
        from .runtime import ExecutorRuntime
        from .required_lane import RequiredLaneRunner
        from .unforced_lane import UnforcedLaneRunner

        resolved_service_port = service_port or TurnFlowBuilder.build_service_port(
            service
        )
        runtime_ops = ExecutorRuntime(
            service_port=resolved_service_port,
            runtime=runtime,
        )
        required_lane = RequiredLaneRunner(
            service_port=resolved_service_port,
            runtime=runtime,
            runtime_ops=runtime_ops,
        )
        unforced_lane = UnforcedLaneRunner(
            service_port=resolved_service_port,
            runtime=runtime,
            runtime_ops=runtime_ops,
        )
        return TurnExecutorComponents(
            service_port=resolved_service_port,
            runtime_ops=runtime_ops,
            required_lane=required_lane,
            unforced_lane=unforced_lane,
        )

    @staticmethod
    def build_turn_executor(
        *,
        service: Any | None = None,
        service_port: TurnFlowServicePort | None = None,
        runtime: TurnRuntimeContext,
    ) -> Any:
        from .executor import TurnExecutor

        components = TurnFlowBuilder.build_turn_executor_components(
            service=service,
            service_port=service_port,
            runtime=runtime,
        )
        return TurnExecutor(
            runtime=runtime,
            service_port=components.service_port,
            runtime_ops=components.runtime_ops,
            required_lane=components.required_lane,
            unforced_lane=components.unforced_lane,
        )


def build_service_port(service: Any) -> TurnFlowServicePort:
    return TurnFlowBuilder.build_service_port(service)


def build_turn_executor_components(
    *,
    service: Any | None = None,
    service_port: TurnFlowServicePort | None = None,
    runtime: TurnRuntimeContext,
) -> TurnExecutorComponents:
    return TurnFlowBuilder.build_turn_executor_components(
        service=service,
        service_port=service_port,
        runtime=runtime,
    )


def build_turn_executor(
    *,
    service: Any | None = None,
    service_port: TurnFlowServicePort | None = None,
    runtime: TurnRuntimeContext,
) -> Any:
    return TurnFlowBuilder.build_turn_executor(
        service=service,
        service_port=service_port,
        runtime=runtime,
    )


__all__ = [
    "AgentServiceTurnFlowAdapter",
    "TurnFlowBuilder",
    "TurnExecutorComponents",
    "build_service_port",
    "build_turn_executor",
    "build_turn_executor_components",
]
