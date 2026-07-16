from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openminion.cli.status.tool_calls import format_tool_args_preview

from ..overlays import TerminalOverlayPresenter


def format_terminal_approval_prompt(tool_name: str, args: dict[str, Any]) -> str:
    name = str(tool_name or "tool").strip() or "tool"
    args_preview = format_tool_args_preview(name, dict(args or {}))
    call_line = f"{name}({args_preview})" if args_preview else f"{name}()"
    return f"Approval required: {call_line}"


def build_terminal_approval_callback(
    *,
    overlay: TerminalOverlayPresenter,
    session_grants: set[str],
    pause_prompt: Callable[[], Any] | None = None,
    resume_prompt: Callable[[], None] | None = None,
) -> Callable[[str, dict[str, Any], Any], Any]:
    async def approval_callback(
        tool_name: str,
        args: dict[str, Any],
        call_id: Any,
    ) -> bool:
        del call_id
        normalized = str(tool_name or "").strip()
        if normalized and normalized in session_grants:
            return True
        prompt = format_terminal_approval_prompt(normalized, dict(args or {}))
        if callable(pause_prompt):
            await pause_prompt()
        try:
            decision = await overlay.present_approval_async(prompt)
        finally:
            if callable(resume_prompt):
                resume_prompt()
        if decision == "always" and normalized:
            session_grants.add(normalized)
            return True
        return decision == "allow"

    return approval_callback
