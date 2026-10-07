from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.text import Text

from openminion.cli.interactive.project_context import (
    find_project_context_target_root,
    write_init_template,
)
from openminion.cli.presentation.styles import StyleToken
from openminion.modules.policy.models import build_policy_facts
from openminion.cli.presentation.markers import token_rich_style
from ..overlays import TerminalOverlayPresenter
from ..status_line import TerminalStatusLine


def run_init_command(
    *,
    runtime: Any,
    console: Console,
    overlay: TerminalOverlayPresenter,
    working_dir: str,
) -> None:
    agent_id = str(getattr(runtime, "agent_id", "") or "openminion").strip()
    target = find_project_context_target_root(working_dir) / "OPENMINION.md"
    decision = overlay.present_approval(
        f"Create OPENMINION.md for this project?\nPath: {target}"
    )
    if decision not in ("allow", "always"):
        console.print(
            Text(
                "(init cancelled)",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
        return
    try:
        target = write_init_template(working_dir=working_dir, agent_id=agent_id)
    except FileExistsError as exc:
        console.print(
            Text(
                f"(project context file already exists: {exc})",
                style=token_rich_style(StyleToken.MUTED, italic=True),
            )
        )
        return
    except (OSError, TypeError, ValueError) as exc:
        console.print(
            Text(
                f"(could not write OPENMINION.md: {exc})",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    setter = getattr(runtime, "set_project_context", None)
    if callable(setter):
        try:
            from openminion.cli.interactive.project_context import (
                resolve_project_context,
            )

            setter(resolve_project_context(working_dir))
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    console.print(
        Text(
            f"(wrote {target})",
            style=token_rich_style(StyleToken.MUTED, italic=True),
        )
    )


def run_slash_goal(
    text: str,
    *,
    runtime: Any,
    console: Console,
    status_line: TerminalStatusLine,
) -> None:
    executor = getattr(runtime, "execute_goal_command", None)
    if not callable(executor):
        console.print(
            Text(
                "(/goal: runtime does not expose goal commands)",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    try:
        tone, body = executor(text)
    except (OSError, RuntimeError, ValueError) as exc:
        tone, body = ("error", f"/goal failed: {exc}")
    token = StyleToken.ERROR if tone == "error" else StyleToken.SYSTEM
    console.print(Text(body, style=token_rich_style(token)))
    label_getter = getattr(runtime, "goal_statusline_label", None)
    if callable(label_getter):
        status_line.set_state(custom=label_getter())


async def run_slash_project(
    text: str,
    *,
    runtime: Any,
    console: Console,
    approval_callback: Callable[..., Any] | None,
) -> None:
    from openminion.cli.commands.autonomy_project import focus_project_help

    if text.strip() in {"/project", "/project help", "/project start --help"}:
        console.print(
            Text(focus_project_help(), style=token_rich_style(StyleToken.SYSTEM))
        )
        return
    if text.split()[1] in {
        "status",
        "show",
        "report",
        "pause",
        "resume",
        "cancel",
        "redirect",
        "answer",
        "reprioritize",
        "extend-budget",
    }:
        try:
            tone, body = runtime.execute_project_control(text)
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            tone, body = ("error", f"/project failed: {exc}")
        token = StyleToken.ERROR if tone == "error" else StyleToken.SYSTEM
        console.print(Text(body, style=token_rich_style(token)))
        return
    if approval_callback is None:
        console.print(
            Text(
                "(/project: approval is unavailable)",
                style=token_rich_style(StyleToken.ERROR),
            )
        )
        return
    try:
        request = runtime.prepare_project_command(text)
        approved = bool(
            await approval_callback(
                "project.start",
                runtime.project_launch_approval_args(request),
                request.run.run_id,
                build_policy_facts(
                    canonical_tool="project.start",
                    reason_code="project_start_approval",
                    risk={
                        "risk_class": "state_change",
                        "side_effects": "local",
                        "reversibility": "unknown",
                    },
                    duration_options=["allow_once", "deny"],
                ),
            )
        )
        tone, body = (
            runtime.launch_prepared_project(request)
            if approved
            else runtime.deny_prepared_project(request)
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        tone, body = ("error", f"/project failed: {exc}")
    token = StyleToken.ERROR if tone == "error" else StyleToken.SYSTEM
    console.print(Text(body, style=token_rich_style(token)))
