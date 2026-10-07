import asyncio
from collections.abc import Callable
from typing import Any

from openminion.cli.status.tool_calls import format_tool_args_preview
from openminion.modules.tool.plugin_api import (
    is_policy_authorization_pair,
    stable_invocation_hash,
)

from ..overlays import TerminalOverlayPresenter


def format_terminal_approval_prompt(tool_name: str, args: dict[str, Any]) -> str:
    name = str(tool_name or "tool").strip() or "tool"
    if name.startswith("sidecar.") and name.endswith(".autostart"):
        sidecar = str(args.get("sidecar", "") or "local service").strip()
        return f"Approval required: start local {sidecar} service and continue"
    if name == "browser" and args.get("op") == "tab.upload":
        files = [
            str(item).strip() for item in args.get("files", []) if str(item).strip()
        ]
        label = ", ".join(files) or "selected files"
        tab_id = str(args.get("tab_id", "") or "").strip()
        destination = f"browser tab {tab_id}" if tab_id else "the current browser tab"
        return f"Approval required: upload {label} to {destination}"
    full_command = (
        name.lower().startswith(("exec.", "git.")) or name == "ops.command.run"
    )
    args_preview = format_tool_args_preview(
        name,
        dict(args or {}),
        compact=not full_command,
    )
    call_line = f"{name}({args_preview})" if args_preview else f"{name}()"
    if _requires_one_time_approval(name):
        return (
            f"Approval required: {call_line}\n"
            "This approval is for this exact action once. Allow once or deny."
        )
    return f"Approval required: {call_line}"


def _requires_one_time_approval(tool_name: str) -> bool:
    if tool_name == "ops.command.run":
        return True
    tool, method = (
        tool_name.rsplit(".", 1) if "." in tool_name else (tool_name, "default")
    )
    return bool(is_policy_authorization_pair(tool, method))


def build_terminal_approval_callback(
    *,
    overlay: TerminalOverlayPresenter,
    session_grants: set[str],
    pause_prompt: Callable[[], Any] | None = None,
    resume_prompt: Callable[[], None] | None = None,
) -> Callable[[str, dict[str, Any], Any], Any]:
    approval_lock = asyncio.Lock()

    async def approval_callback(
        tool_name: str,
        args: dict[str, Any],
        call_id: Any,
    ) -> bool:
        del call_id
        normalized = str(tool_name or "").strip()
        grant_key = stable_invocation_hash(
            tool=normalized,
            method="invoke",
            args=dict(args or {}),
        )
        allow_session_grant = not _requires_one_time_approval(normalized)
        if allow_session_grant and grant_key in session_grants:
            return True
        async with approval_lock:
            if allow_session_grant and grant_key in session_grants:
                return True
            prompt = format_terminal_approval_prompt(normalized, dict(args or {}))
            if callable(pause_prompt):
                await pause_prompt()
            try:
                if _requires_one_time_approval(normalized):
                    return await overlay.present_confirm_async(prompt)
                decision = await overlay.present_approval_async(
                    prompt,
                    always_label=(
                        f"Always allow this exact {normalized} call "
                        "for this shell session"
                    ),
                )
            finally:
                if callable(resume_prompt):
                    resume_prompt()
            if decision == "always":
                session_grants.add(grant_key)
                return True
            return decision == "allow"

    return approval_callback
