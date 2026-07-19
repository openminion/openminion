"""Launch the canonical interactive CLI from config without LLM traffic."""

from __future__ import annotations

import asyncio
import os
import sys
import traceback


async def smoke_focus(config_path: str) -> list[str]:
    errors: list[str] = []
    try:
        from openminion.api.runtime import APIRuntime
        from openminion.cli.interactive.app import FocusApp
        from openminion.cli.interactive.runtime import OpenMinionRuntime

        rt = APIRuntime.from_config_path(config_path)
        adapter = OpenMinionRuntime(rt, target="focus")
        app = FocusApp(runtime=adapter, working_dir=".")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen_name = type(app.screen).__name__
            for key in (
                "ctrl+p",
                "escape",
                "ctrl+f",
                "escape",
                "ctrl+d",
                "ctrl+d",
                "ctrl+t",
                "escape",
            ):
                await pilot.press(key)
                await pilot.pause()
            print(
                f"interactive real-config smoke: launched ({screen_name}); 8 keys dispatched"
            )
    except Exception as exc:
        errors.append(f"interactive: exception during real-config launch — {exc!r}")
        traceback.print_exc()
    return errors


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: interactive_real_config_launch_smoke.py CONFIG_PATH",
            file=sys.stderr,
        )
        return 2
    config_path = argv[1]
    if not os.path.exists(config_path):
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    focus_errors = await smoke_focus(config_path)
    errors = focus_errors
    if errors:
        print("\n=== real-config smoke errors ===", file=sys.stderr)
        for err in errors:
            print(" -", err, file=sys.stderr)
        return 1
    print(
        "real-config smoke: canonical interactive CLI launched against config "
        f"{config_path!r}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
