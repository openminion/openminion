"""Agent execution prompt renderers.

These prompts are domain-owned because they depend on local execution/retry state.
Shared, generic fragments still live in ``openminion.modules.prompting``.
"""

from __future__ import annotations


def build_tool_execution_results_message(
    *, payload: str, extra_feedback: str = "", finalization_guidance: str = ""
) -> str:
    """Render the user-history tool-results message for follow-up turns."""

    message = f"Tool execution results:\n{payload}"
    extra = str(extra_feedback or "").strip()
    if extra:
        message = f"{message}\n\n{extra}"
    guidance = str(finalization_guidance or "").strip()
    if guidance:
        message = f"{message}\n\n{guidance}"
    return message


def build_pre_tool_draft_message_text(*, response_text: str) -> str:
    """Render the pre-tool draft history item used before final-answer retries."""

    return (
        f"Pre-tool draft for the same request (not the final answer):\n{response_text}"
    )


def build_required_tool_retry_prompt(
    *, user_message: str, tool_name: str, required_fields: list[str] | tuple[str, ...]
) -> str:
    """Render the required-tool retry prompt for model-facing tool-call repair."""

    required_hint = ", ".join(required_fields) if required_fields else "none"
    return (
        f"{user_message}\n\n"
        "[CRITICAL TOOL-CALL INSTRUCTION]\n"
        f"You MUST call exactly one tool: '{tool_name}'.\n"
        "Do not answer with plain text.\n"
        f"Required fields to include when applicable: {required_hint}.\n"
        "Return a valid tool call now."
    )


def build_plain_text_retry_feedback(*, payload: str) -> str:
    """Render retry feedback when the model returned tool markup instead of text."""

    return (
        f"Tool execution results:\n{payload}\n\n"
        "Do not emit any tool call markup, channel envelope, JSON tool payload, "
        "or structured tool request. Use the existing tool results already in "
        "context and return only the final user-facing answer text."
    )


def build_plain_text_retry_user_message(*, base_prompt: str) -> str:
    """Render the retry user message asking for plain final text."""

    return (
        f"{base_prompt}\n\n"
        "Return a plain-text answer only. Do not emit any tool call markup "
        "or envelope text."
    )


def build_tool_envelope_retry_user_message(*, base_prompt: str) -> str:
    """Render the second-pass retry user message for envelope-markup leakage."""

    return (
        f"{base_prompt}\n\n"
        "The previous answer was blocked because it was still tool-envelope "
        "markup. Do not mention the blocked envelope. Return only the final "
        "user-facing answer from the tool results already provided."
    )


def build_stale_draft_retry_feedback(*, payload: str) -> str:
    """Render feedback when the model repeats the pre-tool draft."""

    return (
        f"Tool execution results:\n{payload}\n\n"
        "Your previous answer repeated the pre-tool draft instead of using the "
        "tool results. Do not repeat the draft. Use only the tool results already "
        "in context and return the actual final user-facing answer now."
    )


def build_stale_draft_retry_user_message(*, base_prompt: str) -> str:
    """Render the retry user message for stale pre-tool draft echo."""

    return (
        f"{base_prompt}\n\n"
        "Do not repeat the pre-tool draft. Use the tool results and return "
        "the final user-facing answer."
    )


def build_finalization_status_retry_feedback(*, payload: str, guidance: str) -> str:
    """Render retry feedback when typed finalization status was omitted."""

    return f"Tool execution results:\n{payload}\n\n{guidance}"


def build_finalization_status_retry_user_message(
    *, base_prompt: str, guidance: str
) -> str:
    """Render the retry user message for typed finalization status repair."""

    return f"{base_prompt}\n\n{guidance}"


def build_duplicate_final_tool_call_feedback(
    *, payload: str, unavailable_instruction: str = ""
) -> str:
    """Render feedback when a final response repeats an already-run tool call."""

    message = (
        f"Tool execution results:\n{payload}\n\n"
        "You repeated the exact same tool call after it already ran. Do not "
        "repeat that call. Use the tool results already in context and return "
        "the final answer, or choose a different available tool only if the "
        "existing results are insufficient."
    )
    instruction = str(unavailable_instruction or "").strip()
    if instruction:
        message = f"{message}\n\n{instruction}"
    return message


def build_duplicate_final_tool_call_user_message(*, base_prompt: str) -> str:
    """Render the retry user message for duplicate final tool calls."""

    return (
        f"{base_prompt}\n\n"
        "Do not repeat the same tool call. Replan from the existing "
        "tool results."
    )


def build_duplicate_tool_replan_feedback(*, payload: str, signature: str) -> str:
    """Render feedback when the unforced lane repeats the same tool signature."""

    return (
        f"Tool execution results:\n{payload}\n\n"
        f"The previous assistant response repeated the same tool-call signature: "
        f"{signature}. Do not repeat that call. Use the existing tool results "
        "to answer, or choose a different available tool only if more evidence "
        "is required."
    )


def build_duplicate_tool_replan_user_message() -> str:
    """Render the user message for unforced duplicate-tool replanning."""

    return "Continue from the existing tool results. Do not repeat the same tool call."


def build_tool_argument_retry_feedback(*, missing_fields: str = "") -> str:
    """Render feedback for invalid tool arguments in the unforced lane."""

    missing_suffix = (
        f" Missing required field(s): {missing_fields}." if missing_fields else ""
    )
    return (
        "The previous tool call had invalid arguments."
        f"{missing_suffix} Retry the same user task with corrected tool arguments. "
        "Do not repeat the invalid arguments."
    )


def build_denied_tool_recovery_hint(
    *, blocked_tool: str, suggested_tool: str, suggested_fix: str = ""
) -> str:
    """Render recovery guidance when policy suggests a safer replacement tool."""

    guidance = (
        f"The previous {blocked_tool} call was blocked by policy. "
        f"Do not repeat it. Retry the same user task using {suggested_tool} "
        "if that structured tool can satisfy the intent."
    )
    fix = str(suggested_fix or "").strip()
    if fix:
        guidance = f"{guidance} {fix}"
    return guidance


__all__ = [
    "build_denied_tool_recovery_hint",
    "build_duplicate_final_tool_call_feedback",
    "build_duplicate_final_tool_call_user_message",
    "build_duplicate_tool_replan_feedback",
    "build_duplicate_tool_replan_user_message",
    "build_finalization_status_retry_feedback",
    "build_finalization_status_retry_user_message",
    "build_plain_text_retry_feedback",
    "build_plain_text_retry_user_message",
    "build_pre_tool_draft_message_text",
    "build_required_tool_retry_prompt",
    "build_stale_draft_retry_feedback",
    "build_stale_draft_retry_user_message",
    "build_tool_argument_retry_feedback",
    "build_tool_envelope_retry_user_message",
    "build_tool_execution_results_message",
]
