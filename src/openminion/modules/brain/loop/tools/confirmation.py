from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter

from openminion.modules.brain.constants import (
    CONFIRMATION_MESSAGE_ARG_LIMIT,
    CONFIRMATION_MESSAGE_ARG_VALUE_LIMIT,
)
from openminion.modules.brain.schemas import Command, WorkingState

_COMMAND_ADAPTER = TypeAdapter(Command)
_CONFIRMATION_REPLAY_QUEUE_KEY = "_confirmation_replay_queue"

_CONFIRMATION_MESSAGE_ARG_KEYS = ("path", "file_path", "command", "cwd", "url")
_SESSION_CONFIRMATION_TOKENS = frozenset(
    {
        "s",
        "session",
        "allow session",
        "allow this session",
        "session allow",
    }
)


def _bounded_confirmation_arg_value(value: Any) -> str:
    text = " ".join(str(value if value is not None else "").split())
    if len(text) <= CONFIRMATION_MESSAGE_ARG_VALUE_LIMIT:
        return text
    return f"{text[: CONFIRMATION_MESSAGE_ARG_VALUE_LIMIT - 3].rstrip()}..."


def _confirmation_arg_preview(command: Command) -> str:
    raw_args = command.args
    items: list[str] = []
    for key in _CONFIRMATION_MESSAGE_ARG_KEYS:
        if key not in raw_args:
            continue
        value = _bounded_confirmation_arg_value(raw_args.get(key))
        if value:
            items.append(f"{key}={value}")
        if len(items) >= CONFIRMATION_MESSAGE_ARG_LIMIT:
            break
    return ", ".join(items)


def _command_payload(command: Command) -> dict[str, Any]:
    return dict(command.model_dump(mode="json"))


def _command_from_payload(payload: Any) -> Command | None:
    if not isinstance(payload, dict):
        return None
    try:
        return _COMMAND_ADAPTER.validate_python(payload)
    except Exception:
        return None


def extract_confirmation_replay_queue(command: Command) -> list[Command]:
    inputs = command.inputs
    if not isinstance(inputs, dict):
        return []
    raw_queue = inputs.get(_CONFIRMATION_REPLAY_QUEUE_KEY)
    if not isinstance(raw_queue, list):
        return []
    commands: list[Command] = []
    for payload in raw_queue:
        queued_command = _command_from_payload(payload)
        if queued_command is not None:
            commands.append(queued_command)
    return commands


def strip_confirmation_replay_queue(command: Command) -> Command:
    cloned = command.model_copy(deep=True)
    inputs = dict(cloned.inputs or {})
    inputs.pop(_CONFIRMATION_REPLAY_QUEUE_KEY, None)
    cloned.inputs = inputs
    return cloned


def attach_confirmation_replay_queue(
    command: Command,
    queued_commands: Sequence[Command],
) -> Command:
    cloned = strip_confirmation_replay_queue(command)
    payloads = [_command_payload(queued_command) for queued_command in queued_commands]
    if not payloads:
        return cloned
    inputs = dict(cloned.inputs or {})
    inputs[_CONFIRMATION_REPLAY_QUEUE_KEY] = payloads
    cloned.inputs = inputs
    return cloned


def confirmation_replay_batch_size(command: Command) -> int:
    return 1 + len(extract_confirmation_replay_queue(command))


def is_session_confirmation_response(text: str) -> bool:
    token = " ".join(str(text or "").strip().lower().rstrip(".,!?").split())
    return token in _SESSION_CONFIRMATION_TOKENS


def apply_session_confirmation_grant(state: WorkingState, command: Command) -> bool:
    tool_name = str(command.tool_name or "").strip().lower()
    if not tool_name:
        return False
    overrides = dict(state.permission_overrides)
    # "session" means future calls for this tool should stop re-prompting within
    # the current session, not merely widen to the narrower "auto" allowlist.
    overrides[tool_name] = "bypass"
    state.permission_overrides = overrides
    return True


def confirmation_required_user_message(command: Command) -> str:
    tool_name = str(command.tool_name or "tool").strip() or "tool"
    title = str(command.title or "").strip()
    subject = tool_name
    if title and title != tool_name:
        subject = f"{tool_name}: {title}"
    arg_preview = _confirmation_arg_preview(command)
    if arg_preview:
        subject = f"{subject} ({arg_preview})"
    lines = ["Policy confirmation required.", subject]
    additional_count = max(0, confirmation_replay_batch_size(command) - 1)
    if additional_count:
        noun = "command" if additional_count == 1 else "commands"
        lines.append(
            f"This approval also covers {additional_count} queued {noun} from the same batch."
        )
    lines.append(
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel."
    )
    return "\n".join(lines)
