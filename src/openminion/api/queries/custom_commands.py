"""Session-scoped custom command discovery for non-terminal clients."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.api.config import close_api_runtime_if_owned, resolve_api_runtime
from openminion.api.runtime import APIRuntime
from openminion.cli.presentation.custom_commands import (
    CustomCommand,
    command_requires_local_expansion,
    discover_custom_commands,
    render_command_arguments,
)
from openminion.cli.presentation.slash_commands import terminal_slash_commands


@dataclass
class CustomCommandQueryError(RuntimeError):
    message: str
    code: str

    def __str__(self) -> str:
        return self.message


def list_session_custom_commands(
    *, config_path: str | None, session_id: str, runtime: APIRuntime | None
) -> dict[str, Any]:
    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path, runtime=runtime
    )
    try:
        commands = _session_commands(active_runtime, session_id=session_id)
        return {
            "session_id": session_id,
            "commands": [_public_command(command) for command in commands.values()],
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def render_session_custom_command(
    *,
    config_path: str | None,
    session_id: str,
    name: str,
    arguments: str,
    runtime: APIRuntime | None,
) -> dict[str, Any]:
    active_runtime, own_runtime = resolve_api_runtime(
        config_path=config_path, runtime=runtime
    )
    try:
        commands = _session_commands(active_runtime, session_id=session_id)
        command_name = f"/{str(name or '').strip().removeprefix('/')}"
        command = commands.get(command_name)
        if command is None:
            raise CustomCommandQueryError(
                f"Custom command '{command_name}' was not found.",
                "custom_command_not_found",
            )
        if command_requires_local_expansion(command):
            raise CustomCommandQueryError(
                "This command requires terminal-local file or shell expansion.",
                "unsupported_custom_command_expansion",
            )
        try:
            prompt = render_command_arguments(command, arg_string=arguments)
        except ValueError as exc:
            raise CustomCommandQueryError(
                str(exc), "invalid_custom_command_arguments"
            ) from exc
        return {"session_id": session_id, "name": command.slash, "prompt": prompt}
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def _session_commands(
    runtime: APIRuntime, *, session_id: str
) -> dict[str, CustomCommand]:
    session = runtime.sessions.get_session(session_id)
    if session is None:
        raise CustomCommandQueryError(
            f"Session '{session_id}' was not found.", "session_not_found"
        )
    workspace = str(session.metadata.get("workspace_root", "")).strip()
    if not workspace and runtime.tool_workspace_root is not None:
        workspace = str(runtime.tool_workspace_root)
    project_dir = Path(workspace) / ".openminion" / "commands" if workspace else None
    user_dir = Path(runtime.data_root) / "commands"
    commands = discover_custom_commands(project_dir=project_dir, user_dir=user_dir)
    built_ins = set(terminal_slash_commands())
    return {
        name: command
        for name, command in sorted(commands.items())
        if name not in built_ins
    }


def _public_command(command: CustomCommand) -> dict[str, str]:
    return {
        "name": command.slash,
        "description": command.description or "custom command",
        "usage": command.usage or command.slash,
        "source": command.source,
    }


__all__ = [
    "CustomCommandQueryError",
    "list_session_custom_commands",
    "render_session_custom_command",
]
