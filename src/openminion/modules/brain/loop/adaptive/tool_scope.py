"""Adaptive tool-scope filtering and public label helpers."""

import sys
from typing import Any

from openminion.modules.brain.constants import BRAIN_INTERNAL_MODE_ACT_ADAPTIVE
from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_mode_name,
)
from openminion.modules.brain.loop.tools import DirectToolTurnContext

_SEEDED_REPLAY_CONTROL_TOOL_PREFIXES = ("plan.",)
_SEEDED_REPLAY_CONTROL_TOOLS = frozenset({"decompose"})


def _entry_selected_tool_names(entry_response: Any | None) -> set[str]:
    return {
        str(getattr(call, "name", "") or "").strip()
        for call in list(getattr(entry_response, "tool_calls", []) or [])
        if str(getattr(call, "name", "") or "").strip()
    }


def _with_direct_tool_requested_allowed_tools(
    tool_names: frozenset[str],
    direct_tool_turn: DirectToolTurnContext | None,
) -> frozenset[str]:
    if direct_tool_turn is None:
        return tool_names
    requested = {
        name.strip() for name in direct_tool_turn.requested_tool_names if name.strip()
    }
    if not requested:
        return tool_names
    return frozenset({*tool_names, *requested})


def _with_entry_selected_allowed_tools(
    tool_names: frozenset[str],
    *,
    decision_reason_code: str,
    entry_response: Any | None,
) -> frozenset[str]:
    if str(decision_reason_code or "").strip() != "entry_tool_call":
        return tool_names
    return frozenset({*tool_names, *_entry_selected_tool_names(entry_response)})


def _with_requested_allowed_tools(
    tool_names: frozenset[str],
    *,
    direct_tool_turn: DirectToolTurnContext | None,
    decision_reason_code: str,
    entry_response: Any | None,
) -> frozenset[str]:
    tool_names = _with_direct_tool_requested_allowed_tools(tool_names, direct_tool_turn)
    return _with_entry_selected_allowed_tools(
        tool_names,
        decision_reason_code=decision_reason_code,
        entry_response=entry_response,
    )


def _prepare_entry_selected_tool_scope(
    full_tool_specs: list[Any],
    entry_response: Any | None,
    scratchpad: dict[str, Any],
) -> tuple[list[Any], list[Any]]:
    requestable_tool_specs = list(full_tool_specs)
    scratchpad.update(
        {
            "tool_schema_shortlisting.enabled": True,
            "tool_schema_shortlisting.reason": "entry_selected_tool",
            "tool_schema_shortlisting.candidate_count": len(full_tool_specs),
            "tool_schema_shortlisting.active_count": 0,
            "tool_schema_shortlisting.llm_call_made": False,
        }
    )
    selected_names = _entry_selected_tool_names(entry_response)
    active_tool_specs = [
        spec
        for spec in requestable_tool_specs
        if str(getattr(spec, "name", "") or "").strip() in selected_names
    ]
    return active_tool_specs, requestable_tool_specs


def _without_control_tool_names(tool_names: frozenset[str]) -> frozenset[str]:
    return frozenset(
        tool
        for tool in tool_names
        if tool not in _SEEDED_REPLAY_CONTROL_TOOLS
        and not any(
            str(tool).startswith(prefix)
            for prefix in _SEEDED_REPLAY_CONTROL_TOOL_PREFIXES
        )
    )


def _adaptive_public_attr(name: str, fallback: Any) -> Any:
    public_module = sys.modules.get("openminion.modules.brain.loop.adaptive")
    if public_module is None:
        return fallback
    return getattr(public_module, name, fallback)


def _public_act_label() -> str:
    return public_mode_name_for_mode_name(BRAIN_INTERNAL_MODE_ACT_ADAPTIVE) or "act"


def _public_act_tag() -> str:
    return f"[{_public_act_label()}]"
