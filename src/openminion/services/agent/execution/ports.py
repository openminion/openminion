from typing import Any, Protocol
from collections.abc import Mapping

from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage as ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall as ProviderToolCall,
    ProviderToolSpec,
)
from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.modules.policy import ToolBudgetState


class TurnFlowServicePort(Protocol):
    @property
    def provider(self) -> Any: ...

    @property
    def logger(self) -> Any: ...

    @property
    def home_root(self) -> Any: ...

    @property
    def identity_agent_id(self) -> str: ...

    @property
    def config(self) -> Any: ...

    @property
    def tools(self) -> Any | None: ...

    @property
    def tool_selection(self) -> Any | None: ...

    @property
    def identity_tool_filter(self) -> Any: ...

    @property
    def security_policy(self) -> Any | None: ...

    @property
    def self_improvement(self) -> Any | None: ...

    async def generate_normalized(
        self, request: ProviderRequest
    ) -> ProviderResponse: ...

    def get_spec_for_tool(self, tool_name: str) -> ProviderToolSpec | None: ...

    def build_required_tool_retry_prompt(
        self,
        *,
        user_message: str,
        tool_name: str,
        spec: ProviderToolSpec | None,
    ) -> str: ...

    def normalize_required_tool_arguments(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def sanitize_arguments_for_spec(
        self,
        *,
        arguments: Mapping[str, Any],
        spec: ProviderToolSpec | None,
    ) -> dict[str, Any]: ...

    def build_direct_fallback_arguments(
        self,
        *,
        tool_name: str,
        spec: ProviderToolSpec | None,
        inbound: Any,
    ) -> dict[str, Any] | None: ...

    def execute_direct_tool_fallback(
        self,
        *,
        tool_name: str,
        spec: ProviderToolSpec | None,
        inbound: Any,
    ) -> ToolExecutionBatch | None: ...

    def execute_single_tool_call(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
        source: str,
    ) -> ToolExecutionBatch: ...

    def fallback_eligibility_reason(
        self, result: ToolExecutionResult
    ) -> str | None: ...

    def empty_tool_resolution_metadata(self) -> dict[str, str]: ...

    def augment_browser_fallback_chain(
        self,
        *,
        fallback_chain: list[str],
    ) -> list[str]: ...


class RuntimeOpsPort(Protocol):
    async def call_provider(
        self,
        request: ProviderRequest,
        *,
        tool_call_strategy: str,
    ) -> ProviderResponse: ...

    def _build_tool_execution_context(self) -> ToolExecutionContext: ...

    def _collect_batch_output(self, batch: ToolExecutionBatch) -> str: ...

    async def execute_tool_calls(
        self,
        tool_calls: list[Any],
        *,
        tool_budget_state: ToolBudgetState | None,
        context_metadata_overrides: Mapping[str, Any] | None = None,
    ) -> tuple[ToolExecutionBatch, list[dict[str, str]], bool]: ...

    def record_self_improvement(
        self,
        *,
        user_message: str,
        tool_results: list[ToolExecutionResult],
    ) -> None: ...

    def record_argument_failure(
        self,
        *,
        tool_name: str,
        missing_fields: str,
        user_message: str,
    ) -> None: ...
