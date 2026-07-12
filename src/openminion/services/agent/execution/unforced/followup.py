from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
)
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.services.agent.constants import (
    DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
)
from openminion.services.agent.execution.finalization import (
    FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE,
)
from openminion.services.agent.execution.followup import (
    available_follow_up_tools,
)
from ..prompts import (
    build_denied_tool_recovery_hint,
    build_pre_tool_draft_message_text,
    build_tool_execution_results_message,
)

from ..dependencies import ExecutorDeps


def build_follow_up_request(
    runner,
    *,
    deps: ExecutorDeps,
    response,
    batch: ToolExecutionBatch,
    require_typed_finalization: bool = False,
    extra_tool_feedback: str | None = None,
) -> ProviderRequest:
    tool_feedback_payload = deps.tool_batch_metadata(
        batch=batch,
        tool_calls_count=len(response.tool_calls or []),
    ).get("tool_results", "[]")
    tool_feedback_message = build_tool_execution_results_message(
        payload=str(tool_feedback_payload),
        extra_feedback=str(extra_tool_feedback or ""),
        finalization_guidance=(
            FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE if require_typed_finalization else ""
        ),
    )
    tool_history_entry = ProviderHistoryMessage(
        role="user",
        content=tool_feedback_message,
    )
    return ProviderRequest(
        user_message=DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
        system_prompt=runner.runtime.system_prompt,
        history=runner.runtime.provider_history
        + [
            ProviderHistoryMessage(
                role="assistant",
                content=build_pre_tool_draft_message_text(
                    response_text=str(getattr(response, "text", "") or "")
                ),
            ),
            tool_history_entry,
        ],
        tools=available_follow_up_tools(runner),
        metadata={
            "identity_context": "retained",
        },
    )


def denied_tool_recovery_hint(batch: ToolExecutionBatch) -> str | None:
    def _tool_error_details(data: object) -> dict[str, object]:
        if not isinstance(data, dict):
            return {}
        raw_details = data.get("error_details")
        if isinstance(raw_details, dict):
            return dict(raw_details)
        raw_error = data.get("error")
        if isinstance(raw_error, dict):
            nested_details = raw_error.get("details")
            if isinstance(nested_details, dict):
                return dict(nested_details)
        return {}

    for result in list(getattr(batch, "results", []) or []):
        if bool(getattr(result, "ok", False)):
            continue
        data = getattr(result, "data", {})
        if not isinstance(data, dict):
            continue
        error_code = str(data.get("error_code", "") or "").strip().upper()
        details = _tool_error_details(data)
        if not details or error_code != "POLICY_DENIED":
            continue
        suggested_tool = str(details.get("suggested_tool", "") or "").strip()
        suggested_fix = str(details.get("suggested_fix", "") or "").strip()
        if not suggested_tool:
            continue
        blocked_tool = str(getattr(result, "tool_name", "") or "").strip() or "tool"
        return build_denied_tool_recovery_hint(
            blocked_tool=blocked_tool,
            suggested_tool=suggested_tool,
            suggested_fix=suggested_fix,
        )
    return None


__all__ = ["build_follow_up_request", "denied_tool_recovery_hint"]
