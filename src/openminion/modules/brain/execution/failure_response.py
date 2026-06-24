from typing import TYPE_CHECKING, Any

from openminion.modules.tool.contracts.model_ids import MODEL_WEATHER

from ..schemas import WorkingState
from ..tools.parser import normalize_tool_name_for_brain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def build_time_sensitive_failure_response(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Any,
    action_result: Any,
) -> str:
    user_query = getattr(state, "last_user_input", "") or getattr(
        state,
        "goal",
        "your request",
    )

    is_time_sensitive = runner._is_time_sensitive_tool_command(command)
    normalized_tool_name = (
        normalize_tool_name_for_brain(str(getattr(command, "tool_name", "") or ""))
        or str(getattr(command, "tool_name", "") or "").strip().lower()
    )
    is_weather = is_time_sensitive and normalized_tool_name == MODEL_WEATHER
    tool_type = "weather" if is_weather else "current information"

    error_code = ""
    if action_result.error and action_result.error.code:
        error_code = f" [{action_result.error.code}]"

    lines = [
        f"**{tool_type.capitalize()} Request Failed**{error_code}",
        "",
        f'I was unable to retrieve current {tool_type} data for your query: "{user_query[:100]}"',
        "",
        "**Possible reasons:**",
        "• The external service may be temporarily unavailable",
        "• API rate limits may have been exceeded",
        "• Network connectivity issues",
        "",
        "**Retry options:**",
        "• Try again in a few moments",
        "• Check your internet connection",
    ]

    if is_weather:
        lines.extend(
            [
                "• Verify the location name is spelled correctly",
                "• Try using the full city name (e.g., 'san francisco' instead of 'sf')",
            ]
        )

    lines.extend(
        [
            "",
            f"_Error: Tool execution failed after exhausting retries. Stale or estimated {tool_type} data is unavailable._",
        ]
    )

    return "\n".join(lines)


__all__ = ["build_time_sensitive_failure_response"]
