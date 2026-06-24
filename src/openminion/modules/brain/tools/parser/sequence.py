"""Shared explicit tool-sequence parsing for brain-owned act recovery."""

import re

from .base import normalize_tool_name_for_brain

_BACKTICKED_TOOL_TOKEN_RE = re.compile(r"`([^`]+)`")
_EXPLICIT_TOOL_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9_.-]*\b")
_PLAN_ACTION_RE = re.compile(r"""action\s*=\s*["'](?P<action>[a-z_]+)["']""")
_PLAN_ACTION_TO_TOOL_NAME = {
    "declare": "plan",
    "set": "plan",
    "add": "plan",
    "update": "plan",
    "complete": "plan",
    "list": "plan",
    "clear": "plan",
    "step_completed": "plan",
    "step_blocked": "plan",
    "revise": "plan",
    "abandon": "plan",
}


def _append_explicit_tool_matches(
    text: str,
    *,
    token_matches: list[re.Match[str]],
    token_getter,
    matches: list[str],
) -> None:
    for index, match in enumerate(token_matches):
        token = str(token_getter(match) or "").strip()
        if not token:
            continue
        canonical = normalize_tool_name_for_brain(token)
        if canonical:
            matches.append(canonical)
            continue
        if token.lower() != "plan":
            continue
        next_start = (
            token_matches[index + 1].start()
            if index + 1 < len(token_matches)
            else len(text)
        )
        action_match = _PLAN_ACTION_RE.search(text[match.end() : next_start])
        if action_match is None:
            continue
        action = str(action_match.group("action") or "").strip().lower()
        mapped = _PLAN_ACTION_TO_TOOL_NAME.get(action)
        if mapped:
            matches.append(mapped)


def explicit_tool_name_sequence(user_text: str) -> tuple[str, ...]:
    """Return the ordered explicit tool sequence requested by the user text."""

    text = str(user_text or "")
    matches: list[str] = []
    _append_explicit_tool_matches(
        text,
        token_matches=list(_EXPLICIT_TOOL_TOKEN_RE.finditer(text)),
        token_getter=lambda match: match.group(0),
        matches=matches,
    )

    if matches:
        return tuple(matches)

    _append_explicit_tool_matches(
        text,
        token_matches=list(_BACKTICKED_TOOL_TOKEN_RE.finditer(text)),
        token_getter=lambda match: match.group(1),
        matches=matches,
    )
    return tuple(matches)


__all__ = ["explicit_tool_name_sequence"]
