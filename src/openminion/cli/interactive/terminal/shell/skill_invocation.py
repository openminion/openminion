from collections.abc import Awaitable, Callable
from typing import Any

from openminion.cli.presentation.models import ChatMessage, MessageKind
from openminion.cli.presentation.slash_commands import render_skill_invocation

from ..transcript import TerminalTranscript

StartTurn = Callable[..., Awaitable[None]]


async def handle_skill_invocation(
    slash_arg: str,
    runtime: Any,
    transcript: TerminalTranscript,
    start_turn: StartTurn,
) -> bool:
    parts = slash_arg.split(maxsplit=1)
    if not parts:
        transcript.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=(
                    "Usage: /skill <skill_id> [task]\n"
                    "Use /skills to list available skills."
                ),
            )
        )
        return False

    skill_id = parts[0]
    available_ids = {
        str(row.get("id", "")).strip()
        for row in runtime.list_skill_rows()
        if row.get("source") == "catalog" and str(row.get("id", "")).strip()
    }
    if skill_id not in available_ids:
        transcript.push_message(
            ChatMessage(
                kind=MessageKind.SYSTEM,
                sender="system",
                body=(
                    f"Skill not found: {skill_id}\n"
                    "Use /skills to list available skills."
                ),
            )
        )
        return False

    task = parts[1] if len(parts) > 1 else ""
    rendered = render_skill_invocation(skill_id, task)
    transcript.push_message(
        ChatMessage(kind=MessageKind.USER, sender="you", body=rendered),
        render=False,
    )
    await start_turn(
        rendered,
        inbound_metadata={"explicit_skill_id": skill_id},
    )
    return False


__all__ = ["StartTurn", "handle_skill_invocation"]
