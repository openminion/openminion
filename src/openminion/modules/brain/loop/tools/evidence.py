from __future__ import annotations

from typing import Any

from .contracts import AdaptiveToolLoopState
from .plan_control import PLAN_TOOL_NAME
from .shortlisting import TOOL_REQUEST_TOOL_NAME

_CONTROL_TOOL_NAMES = frozenset({PLAN_TOOL_NAME, TOOL_REQUEST_TOOL_NAME, "decompose"})


def _normalized_tool_name(tool_name: Any) -> str:
    return str(tool_name or "").strip()


def _is_substantive_tool_name(tool_name: Any) -> bool:
    name = _normalized_tool_name(tool_name)
    if not name:
        return False
    root = name.split(".", 1)[0]
    return name not in _CONTROL_TOOL_NAMES and root not in _CONTROL_TOOL_NAMES


def _loop_tool_result_payloads(
    loop_state: AdaptiveToolLoopState,
) -> list[dict[str, Any]]:
    return [
        item
        for item in list(loop_state.scratchpad.get("adaptive.tool_results", []) or [])
        if isinstance(item, dict)
    ]


def _substantive_tool_results(
    loop_state: AdaptiveToolLoopState,
) -> list[dict[str, Any]]:
    return [
        item
        for item in _loop_tool_result_payloads(loop_state)
        if _is_substantive_tool_name(item.get("tool_name"))
    ]


def _successful_substantive_tool_results(
    loop_state: AdaptiveToolLoopState,
) -> list[dict[str, Any]]:
    return [
        item
        for item in _substantive_tool_results(loop_state)
        if bool(item.get("ok"))
    ]


def _count_substantive_non_control_tool_results(
    loop_state: AdaptiveToolLoopState,
) -> int:
    return len(_substantive_tool_results(loop_state))


def _loop_has_non_success_tool_result(loop_state: AdaptiveToolLoopState) -> bool:
    for item in _loop_tool_result_payloads(loop_state):
        if not bool(item.get("ok")):
            return True
    return False
