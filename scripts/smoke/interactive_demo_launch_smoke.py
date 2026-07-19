"""Launch the canonical interactive CLI through the demo runtime."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import traceback


async def smoke_focus(tmp_path: str) -> list[str]:
    errors: list[str] = []
    try:
        from openminion.cli.interactive.app import FocusApp, _DemoFocusRuntime
        from openminion.cli.interactive.widgets import (
            FocusComposer,
            FocusStatusLine,
            FocusTranscript,
        )

        runtime = _DemoFocusRuntime(working_dir=tmp_path)
        app = FocusApp(runtime=runtime, working_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            if not list(app.screen.query(FocusTranscript)):
                errors.append("interactive: no FocusTranscript mounted")
            if not list(app.screen.query(FocusComposer)):
                errors.append("interactive: no FocusComposer mounted")
            # focus chrome consolidated into FocusStatusLine
            if not list(app.screen.query(FocusStatusLine)):
                errors.append("interactive: no FocusStatusLine mounted")
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
        errors.append(f"interactive: exception during launch — {exc!r}")
        traceback.print_exc()
    return errors


async def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        focus_errors = await smoke_focus(tmp)
    errors = focus_errors
    if errors:
        print("\n=== smoke errors ===")
        for err in errors:
            print(" -", err)
        return 1
    print("smoke: canonical interactive CLI launched and handled key cycles.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
