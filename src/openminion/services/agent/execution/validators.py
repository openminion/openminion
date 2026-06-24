from typing import Any, Callable

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.dispatch import _get_registry_manager

from ..errors import collect_missing_required, extract_missing_fields, is_argument_error
from ..prompt_history import _looks_like_tool_call_envelope_text


def is_empty_provider_response(response: Any) -> bool:
    return (
        not str(getattr(response, "text", "") or "").strip()
        and not list(getattr(response, "tool_calls", None) or [])
        and not bool(getattr(response, STATE_KEY_FINALIZATION_STATUS, None))
    )


SpecLookup = Callable[[str], object | None]


def canonical_tool_name(tool_name: str) -> str:
    """Normalize tool name using manager-backed resolution."""
    token = str(tool_name or "").strip()
    if not token:
        return ""
    mgr = _get_registry_manager()
    return mgr.normalize_raw_name(token) or token


def canonical_tool_chain(tool_names: list[str]) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()
    for item in tool_names:
        token = canonical_tool_name(item)
        if not token or token in seen:
            continue
        chain.append(token)
        seen.add(token)
    return chain


def looks_like_tool_call_envelope(text: str) -> bool:
    return _looks_like_tool_call_envelope_text(text)


def collect_missing_required_args(
    tool_calls: list[object], *, spec_lookup: SpecLookup
) -> dict[str, list[str]]:
    return collect_missing_required(tool_calls, spec_lookup=spec_lookup)


def is_tool_argument_error(result: ToolExecutionResult) -> bool:
    return is_argument_error(result)


def extract_missing_argument_fields(results: list[ToolExecutionResult]) -> str:
    return extract_missing_fields(results)
