import asyncio
from collections.abc import Callable
from typing import Any

from openminion.cli.status.tool_calls import format_tool_args_preview
from openminion.base.redaction import redact_mapping, redact_sensitive_text
from openminion.modules.policy.grants import requires_once_duration
from openminion.modules.tool.plugin_api import (
    is_policy_authorization_pair,
)

from ..overlays import TerminalOverlayPresenter


def format_terminal_approval_prompt(
    tool_name: str,
    args: dict[str, Any],
    policy_facts: dict[str, Any] | None = None,
) -> str:
    name = str(tool_name or "tool").strip() or "tool"
    if name.startswith("sidecar.") and name.endswith(".autostart"):
        sidecar = str(args.get("sidecar", "") or "local service").strip()
        first_line = f"Approval required: start local {sidecar} service and continue"
    elif name == "browser" and args.get("op") == "tab.upload":
        files = [
            str(item).strip() for item in args.get("files", []) if str(item).strip()
        ]
        label = ", ".join(files) or "selected files"
        tab_id = str(args.get("tab_id", "") or "").strip()
        destination = f"browser tab {tab_id}" if tab_id else "the current browser tab"
        first_line = f"Approval required: upload {label} to {destination}"
    else:
        full_command = (
            name.lower().startswith(("exec.", "git.")) or name == "ops.command.run"
        )
        args_preview = format_tool_args_preview(
            name,
            redact_mapping(dict(args or {}))[0],
            compact=not full_command,
        )
        call_line = f"{name}({args_preview})" if args_preview else f"{name}()"
        first_line = f"Approval required: {call_line}"
    facts = policy_facts or {}
    lines = [first_line]
    risk = facts.get("risk") if isinstance(facts.get("risk"), dict) else {}
    if risk:
        lines.extend(
            [
                f"Risk: {risk.get('risk_class', 'unknown')}",
                f"Side effects: {risk.get('side_effects', 'unknown')}",
                f"Reversibility: {risk.get('reversibility', 'unknown')}",
            ]
        )
    if facts.get("reason_code"):
        lines.append(f"Reason: {facts['reason_code']}")
    if facts.get("duration_options"):
        lines.append(f"Choices: {', '.join(facts['duration_options'])}")
    if _requires_one_time_approval(name):
        lines.append("This approval is for this exact action once. Allow once or deny.")
    return "\n".join(str(redact_sensitive_text(line)[0]) for line in lines)


def _requires_one_time_approval(tool_name: str) -> bool:
    if tool_name == "ops.command.run":
        return True
    tool, method = (
        tool_name.rsplit(".", 1) if "." in tool_name else (tool_name, "default")
    )
    return bool(requires_once_duration(tool=tool, method=method))


def build_terminal_approval_callback(
    *,
    overlay: TerminalOverlayPresenter,
    policy_ctl: Any | None = None,
    session_id: str = "",
    runtime: Any | None = None,
    pause_prompt: Callable[[], Any] | None = None,
    resume_prompt: Callable[[], None] | None = None,
) -> Callable[[str, dict[str, Any], Any, dict[str, Any] | None], Any]:
    if runtime is not None:
        policy_ctl = getattr(getattr(runtime, "_rt", None), "action_policy", None)
        session_id = str(getattr(runtime, "session_id", "") or "")
    approval_lock = asyncio.Lock()

    async def approval_callback(
        tool_name: str,
        args: dict[str, Any],
        call_id: Any,
        policy_facts: dict[str, Any] | None = None,
    ) -> bool:
        normalized = str(tool_name or "").strip()
        policy_tool, policy_method = (
            normalized.rsplit(".", 1) if "." in normalized else (normalized, "default")
        )
        allow_session_grant = (
            not _requires_one_time_approval(normalized)
            and policy_ctl is not None
            and bool(session_id)
        )

        def policy_disposition() -> str:
            if policy_ctl is None or is_policy_authorization_pair(
                policy_tool, policy_method
            ):
                return "prompt"
            decision = policy_ctl.check(
                {
                    "tool": policy_tool,
                    "method": policy_method,
                    "args": dict(args or {}),
                },
                {"session_id": session_id},
            )
            if decision.decision == "DENY":
                return "deny"
            if (
                allow_session_grant
                and decision.decision == "ALLOW"
                and decision.matched_grant_id
            ):
                return "allow"
            return "prompt"

        disposition = policy_disposition()
        if disposition == "deny":
            return False
        if disposition == "allow":
            return True
        async with approval_lock:
            disposition = policy_disposition()
            if disposition == "deny":
                return False
            if disposition == "allow":
                return True
            prompt = format_terminal_approval_prompt(
                normalized,
                dict(args or {}),
                policy_facts,
            )
            if callable(pause_prompt):
                await pause_prompt()
            try:
                if not allow_session_grant:
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
                assert policy_ctl is not None
                policy_ctl.create_grant_from_confirmation(
                    invocation={
                        "id": str(call_id or normalized),
                        "tool": policy_tool,
                        "method": policy_method,
                        "args": dict(args or {}),
                    },
                    ctx={"session_id": session_id},
                    action="allow_session_exact",
                )
                return True
            return decision == "allow"

    return approval_callback
