"""Launch both TUI shells against a real config without sending LLM traffic."""

from __future__ import annotations

import asyncio
import os
import sys
import traceback


async def smoke_dashboard(config_path: str) -> list[str]:
    errors: list[str] = []
    try:
        from openminion.api.runtime import APIRuntime
        from openminion.cli.parser.contracts import ProviderBundle
        from openminion.cli.tui.app import OpenMinionApp
        from openminion.cli.tui.providers.runtime import OpenMinionRuntime

        rt = APIRuntime.from_config_path(config_path)
        adapter = OpenMinionRuntime(rt, prompt_on_resume=True)
        bundle = ProviderBundle.from_api_runtime(rt)
        app = OpenMinionApp(runtime=adapter, providers=bundle)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            screen_name = type(app.screen).__name__
            errors_seen = list(getattr(app, "_exceptions", []))
            if errors_seen:
                errors.append(f"dashboard: app captured exceptions: {errors_seen!r}")
            # Press every dashboard tab shortcut to exercise compose.
            # Modals on top intercept these keys, but pressing them must
            # not raise.
            for key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
                await pilot.press(f"ctrl+{key}")
                await pilot.pause()
            # Try escape to close any modal so we reach MainScreen at
            # least once before exit.
            for _ in range(3):
                await pilot.press("escape")
                await pilot.pause()
            print(
                f"dashboard real-config smoke: launched ({screen_name}); 9 tab shortcuts dispatched"
            )
    except Exception as exc:
        errors.append(f"dashboard: exception during real-config launch — {exc!r}")
        traceback.print_exc()
    return errors


async def smoke_focus(config_path: str) -> list[str]:
    errors: list[str] = []
    try:
        from openminion.api.runtime import APIRuntime
        from openminion.cli.tui.focus.app import FocusApp
        from openminion.cli.tui.providers.runtime import OpenMinionRuntime

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
                f"focus real-config smoke: launched ({screen_name}); 8 keys dispatched"
            )
    except Exception as exc:
        errors.append(f"focus: exception during real-config launch — {exc!r}")
        traceback.print_exc()
    return errors


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: tui_real_config_launch_smoke.py CONFIG_PATH",
            file=sys.stderr,
        )
        return 2
    config_path = argv[1]
    if not os.path.exists(config_path):
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2

    dash_errors = await smoke_dashboard(config_path)
    focus_errors = await smoke_focus(config_path)
    errors = dash_errors + focus_errors
    if errors:
        print("\n=== real-config smoke errors ===", file=sys.stderr)
        for err in errors:
            print(" -", err, file=sys.stderr)
        return 1
    print(
        "real-config smoke: both shells launched cleanly against config "
        f"{config_path!r}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
