"""Launch both TUI shells through the credential-free demo runtime."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback


async def smoke_dashboard() -> list[str]:
    errors: list[str] = []
    try:
        from openminion.cli.tui.app import DemoRuntime, OpenMinionApp
        from openminion.cli.tui.widgets import ChatView, Sidebar

        app = OpenMinionApp(runtime=DemoRuntime())
        async with app.run_test() as pilot:
            await pilot.pause()
            if not list(app.screen.query(ChatView)):
                errors.append("dashboard: no ChatView mounted")
            if not list(app.screen.query(Sidebar)):
                errors.append("dashboard: no Sidebar mounted")
            # Exercise lazy tab composition, where mount errors often surface.
            for key in ("2", "3", "4", "5", "6", "7", "8", "9", "1"):
                await pilot.press(f"ctrl+{key}")
                await pilot.pause()
    except Exception as exc:
        errors.append(f"dashboard: exception during launch — {exc!r}")
        traceback.print_exc()
    return errors


async def smoke_focus(tmp_path: str) -> list[str]:
    errors: list[str] = []
    try:
        from openminion.cli.tui.focus.app import FocusApp, _DemoFocusRuntime
        from openminion.cli.tui.focus.widgets import (
            FocusComposer,
            FocusStatusLine,
            FocusTranscript,
        )

        runtime = _DemoFocusRuntime(working_dir=tmp_path)
        app = FocusApp(runtime=runtime, working_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            if not list(app.screen.query(FocusTranscript)):
                errors.append("focus: no FocusTranscript mounted")
            if not list(app.screen.query(FocusComposer)):
                errors.append("focus: no FocusComposer mounted")
            # focus chrome consolidated into FocusStatusLine
            if not list(app.screen.query(FocusStatusLine)):
                errors.append("focus: no FocusStatusLine mounted")
            for key in (
                "ctrl+p",
                "escape",
                "ctrl+f",
                "escape",
                "ctrl+k",
                "ctrl+l",
                "ctrl+l",
                "ctrl+d",
                "ctrl+d",
                "ctrl+t",
                "escape",
            ):
                await pilot.press(key)
                await pilot.pause()
    except Exception as exc:
        errors.append(f"focus: exception during launch — {exc!r}")
        traceback.print_exc()
    return errors


async def main() -> int:
    dash_errors = await smoke_dashboard()
    with tempfile.TemporaryDirectory() as tmp:
        focus_errors = await smoke_focus(tmp)
    errors = dash_errors + focus_errors
    if errors:
        print("\n=== smoke errors ===")
        for err in errors:
            print(" -", err)
        return 1
    print("smoke: both shells launched cleanly and responded to key press cycles.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
