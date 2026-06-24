from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.cli.presentation import styles
from openminion.cli.presentation.plan_render import render_plan_envelope
from openminion.tools.todo.plugin import _h_clear, _h_list


def handle_plan_command(line: str, *, session_id: str) -> bool:
    stripped = (line or "").strip()
    ctx: Any = SimpleNamespace(session_id=session_id or "")
    if stripped in {"/plan", "/plan show"}:
        envelope = _h_list({}, ctx)
        print(styles.style(styles.StyleToken.INFO, render_plan_envelope(envelope)))
        return True

    if stripped == "/plan clear":
        envelope = _h_clear({}, ctx)
        print(styles.style(styles.StyleToken.SUCCESS, "Plan cleared."))
        print(styles.style(styles.StyleToken.INFO, render_plan_envelope(envelope)))
        return True

    print(
        styles.style(
            styles.StyleToken.ERROR,
            "usage: /plan [show|clear]",
        )
    )
    return True
