"""Chat-CLI approval callback for confirmation-gated tool execution."""

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from openminion.cli.status.tool_calls import format_tool_args_preview

ApprovalCallback = Callable[[str, dict[str, Any], Any], Awaitable[bool]]


@dataclass
class ChatApprovalState:
    session_grants: set[str] = field(default_factory=set)


def build_chat_approval_callback(
    *,
    state: ChatApprovalState,
    input_fn: Callable[[str], str] = input,
    output_stream=None,
) -> ApprovalCallback:
    stream = output_stream if output_stream is not None else sys.stdout

    async def _callback(
        tool_name: str,
        args: dict[str, Any],
        call_id: Any,
    ) -> bool:
        del call_id
        normalized = str(tool_name or "").strip()
        if normalized and normalized in state.session_grants:
            return True
        prompt = _format_chat_approval_prompt(normalized, dict(args or {}))
        stream.write(prompt + "\n")
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
        choice = await _read_choice(input_fn, prompt_suffix="> ")
        decision = _resolve_choice(choice)
        if decision == "allow_session" and normalized:
            state.session_grants.add(normalized)
        return decision in {"allow_once", "allow_session"}

    return _callback


def _format_chat_approval_prompt(tool_name: str, args: Mapping[str, Any]) -> str:
    args_preview = format_tool_args_preview(tool_name, args)
    name = tool_name or "tool"
    call_line = f"{name}({args_preview})" if args_preview else f"{name}()"
    return (
        "Approval required: "
        f"{call_line}\n"
        "  [a] allow once  [s] allow this session  [d] deny"
    )


async def _read_choice(input_fn: Callable[[str], str], *, prompt_suffix: str) -> str:
    loop = asyncio.get_running_loop()
    raw = await loop.run_in_executor(None, lambda: input_fn(prompt_suffix))
    return str(raw or "").strip().lower()


def _resolve_choice(choice: str) -> str:
    cleaned = str(choice or "").strip().lower()
    if cleaned in {"a", "allow", "y", "yes", "1"}:
        return "allow_once"
    if cleaned in {"s", "session", "2"}:
        return "allow_session"
    return "deny"


__all__ = [
    "ApprovalCallback",
    "ChatApprovalState",
    "build_chat_approval_callback",
]
