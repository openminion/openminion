"""Adaptive tool-scope filtering and public label helpers."""

import re
import sys
from typing import Any

from openminion.modules.brain.constants import BRAIN_INTERNAL_MODE_ACT_ADAPTIVE
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.execution.public_taxonomy import (
    public_mode_name_for_mode_name,
)
from openminion.modules.brain.loop.tools import DirectToolTurnContext

_SEEDED_REPLAY_CONTROL_TOOL_PREFIXES = ("plan.",)
_SEEDED_REPLAY_CONTROL_TOOLS = frozenset({"decompose"})
_EXPLICIT_TOOL_OPT_OUT_TOKEN_PREFIXES = {
    "git": ("git.",),
    "plan": ("plan.",),
}
_EXPLICIT_TOOL_OPT_OUT_TOKEN_NAMES = {
    "decompose": ("decompose",),
    "tool.list": ("tool.list",),
}
_EXPLICIT_TOOL_OPT_OUT_TOKENS = frozenset(
    {
        *_EXPLICIT_TOOL_OPT_OUT_TOKEN_PREFIXES,
        *_EXPLICIT_TOOL_OPT_OUT_TOKEN_NAMES,
    }
)
_CONTROL_TOOL_OPT_OUT_TOKENS = frozenset({"decompose", "plan"})
_NEGATIVE_TOOL_SCOPE_RE = re.compile(r"\bwithout\s+(?P<items>[a-z0-9_.\-/]+)")


def _with_direct_tool_requested_allowed_tools(
    tool_names: frozenset[str],
    direct_tool_turn: DirectToolTurnContext | None,
) -> frozenset[str]:
    if direct_tool_turn is None:
        return tool_names
    requested = {
        str(name or "").strip()
        for name in tuple(getattr(direct_tool_turn, "requested_tool_names", ()) or ())
        if str(name or "").strip()
    }
    if not requested:
        return tool_names
    return frozenset({*tool_names, *requested})


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


def _without_explicit_tool_opt_outs(
    tool_names: frozenset[str],
    *,
    opt_out_tokens: frozenset[str],
) -> frozenset[str]:
    exact_names = frozenset(
        name
        for token in opt_out_tokens
        for name in _EXPLICIT_TOOL_OPT_OUT_TOKEN_NAMES.get(token, ())
    )
    prefixes = tuple(
        prefix
        for token in opt_out_tokens
        for prefix in _EXPLICIT_TOOL_OPT_OUT_TOKEN_PREFIXES.get(token, ())
    )
    return frozenset(
        tool
        for tool in tool_names
        if tool not in exact_names
        and not any(tool.startswith(prefix) for prefix in prefixes)
    )


def _explicit_tool_opt_out_tokens(ctx: ExecutionContext) -> frozenset[str]:
    haystacks = (
        str(getattr(ctx, "user_input", "") or ""),
        str(getattr(getattr(ctx, "state", None), "goal", "") or ""),
        str(getattr(getattr(ctx, "decision", None), "objective", "") or ""),
    )
    normalized = "\n".join(haystacks).lower()
    opt_outs: set[str] = set()
    for match in _NEGATIVE_TOOL_SCOPE_RE.finditer(normalized):
        for token in re.split(r"[/,]+", match.group("items")):
            stripped = token.strip().strip(".;:")
            if stripped in _EXPLICIT_TOOL_OPT_OUT_TOKENS:
                opt_outs.add(stripped)
    for token in _EXPLICIT_TOOL_OPT_OUT_TOKENS:
        token_patterns = (
            f"do not use {token}",
            f"don't use {token}",
            f"do not use the {token} tool",
            f"don't use the {token} tool",
        )
        if any(pattern in normalized for pattern in token_patterns):
            opt_outs.add(token)
    return frozenset(opt_outs)


def _adaptive_public_attr(name: str, fallback: Any) -> Any:
    public_module = sys.modules.get("openminion.modules.brain.loop.adaptive")
    if public_module is None:
        return fallback
    return getattr(public_module, name, fallback)


def _public_act_label() -> str:
    return public_mode_name_for_mode_name(BRAIN_INTERNAL_MODE_ACT_ADAPTIVE) or "act"


def _public_act_tag() -> str:
    return f"[{_public_act_label()}]"
