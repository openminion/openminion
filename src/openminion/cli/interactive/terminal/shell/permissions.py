import json
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.base.redaction import redact_mapping, redact_sensitive_text
from openminion.cli.presentation.markers import token_rich_style
from openminion.cli.presentation.permissions import (
    apply_permission_menu_choice,
    apply_permission_override,
    format_permission_status_label,
)
from openminion.cli.presentation.styles import StyleToken

from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine


def runtime_permission_mode(runtime: Any) -> str:
    return str(getattr(runtime, "permission_mode", "default") or "default").strip()


def runtime_action_policy_mode(runtime: Any) -> str:
    return str(getattr(runtime, "action_policy_mode_override", "") or "").strip()


def runtime_permission_label(runtime: Any) -> str:
    return str(
        format_permission_status_label(
            permission_mode=runtime_permission_mode(runtime),
            action_policy_mode=runtime_action_policy_mode(runtime),
        )
    )


def sync_permission_status(
    runtime: Any, status_line: TerminalStatusLine | None
) -> None:
    if status_line is not None:
        status_line.set_state(
            permission_mode=runtime_permission_mode(runtime),
            action_policy_mode=runtime_action_policy_mode(runtime),
        )


def initialize_permission_status(
    runtime: Any,
    status_line: TerminalStatusLine,
    console: Console,
) -> None:
    sync_permission_status(runtime, status_line)
    diagnostic = str(
        getattr(runtime, "_permission_posture_diagnostic", "") or ""
    ).strip()
    if diagnostic:
        console.print(
            Text(
                f"(permissions: {diagnostic})",
                style=token_rich_style(StyleToken.WARNING),
            )
        )


def cycle_permission_mode(
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine | None,
    announce: bool = True,
) -> str:
    cycler = getattr(runtime, "cycle_permission_mode", None)
    if not callable(cycler):
        raise RuntimeError("runtime does not expose cycle_permission_mode")
    mode = str(cycler() or "default").strip() or "default"
    sync_permission_status(runtime, status_line)
    if announce:
        console.print(
            Text(
                f"(permissions: {runtime_permission_label(runtime)} — Shift+Tab cycles modes)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
    return mode


async def handle_permissions(
    text: str,
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine | None,
    overlay: TerminalOverlayPresenter,
) -> None:
    arg = text.partition(" ")[2].strip().lower()
    if not arg:
        await _choose_posture(runtime, console, status_line, overlay)
        return
    if arg == "cycle":
        cycle_permission_mode(runtime=runtime, console=console, status_line=status_line)
        return
    if arg == "grants":
        _list_grants(runtime, console)
        return
    if arg.startswith("revoke "):
        _revoke_grant(runtime, console, arg.partition(" ")[2].strip())
        return
    parts = arg.split()
    if len(parts) == 2:
        override_result = apply_permission_override(runtime, *parts)
        console.print(Text(f"({override_result.message})", style="dim italic"))
        return
    if arg == "bypass" and not await overlay.present_confirm_async(
        _FULL_ACCESS_CONFIRMATION
    ):
        console.print(Text("(permissions unchanged)", style="dim italic"))
        return
    choice_id = {
        "default": "ask",
        "readonly": "readonly",
        "auto": "auto",
        "bypass": "full_access",
    }.get(arg)
    try:
        if choice_id is None:
            mode = runtime.set_permission_mode(arg)
            message = f"permissions → {mode}"
        else:
            menu_result = apply_permission_menu_choice(
                runtime, choice_id, confirmed=arg == "bypass"
            )
            message = menu_result.message
        sync_permission_status(runtime, status_line)
    except (AttributeError, RuntimeError, ValueError) as exc:
        console.print(Text(f"(/permissions: {exc})", style="bold red"))
        return
    console.print(Text(f"({message})", style="dim italic"))


async def _choose_posture(
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine | None,
    overlay: TerminalOverlayPresenter,
) -> None:
    choice_id = await overlay.present_permission_picker_async()
    if choice_id is None:
        console.print(Text("(permissions unchanged)", style="dim italic"))
        return
    confirmed = choice_id != "full_access" or await overlay.present_confirm_async(
        _FULL_ACCESS_CONFIRMATION
    )
    if not confirmed:
        console.print(Text("(permissions unchanged)", style="dim italic"))
        return
    result = apply_permission_menu_choice(runtime, choice_id, confirmed=True)
    sync_permission_status(runtime, status_line)
    console.print(Text(f"({result.message})", style="dim italic"))


def _list_grants(runtime: Any, console: Console) -> None:
    grants = list(runtime.list_permission_grants() or [])[:50]
    if not grants:
        console.print(Text("(permissions: no active grants)", style="dim italic"))
        return
    for grant in grants:
        target, _ = redact_mapping(dict(grant.target_json or {}))
        scope = json.dumps(target, sort_keys=True, separators=(",", ":")) or "{}"
        rendered, _ = redact_sensitive_text(
            f"{grant.grant_id}  {grant.tool}.{grant.method}  "
            f"scope={scope}  duration={grant.duration_type}"
        )
        console.print(f"{rendered[:237]}..." if len(rendered) > 240 else rendered)


def _revoke_grant(runtime: Any, console: Console, grant_id: str) -> None:
    if runtime.revoke_permission_grant(grant_id):
        console.print(Text(f"(permissions: revoked {grant_id})", style="dim italic"))
    else:
        console.print(
            Text(f"(/permissions: unknown grant {grant_id})", style="bold red")
        )


_FULL_ACCESS_CONFIRMATION = (
    "Full access skips ordinary prompts, but configured block/ask rules, explicit "
    "denials, tool exposure, workspace paths, host execution, secret/credential "
    "boundaries, exact authorization, and project-start approval still apply. "
    "Continue?"
)


__all__ = (
    "cycle_permission_mode",
    "handle_permissions",
    "initialize_permission_status",
    "runtime_permission_label",
    "runtime_permission_mode",
    "runtime_action_policy_mode",
    "sync_permission_status",
)
