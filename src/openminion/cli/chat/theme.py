"""Theme command handling for chat UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from openminion.cli.presentation import styles
from openminion.cli.theme import (
    DARK,
    available_theme_names,
    lookup_theme,
    persisted_theme_path,
    read_persisted_theme,
    resolve_theme,
    write_persisted_theme,
)


def handle_theme(
    *,
    line: str = "/theme",
    data_root: Any = None,
    theme_applier: Callable[[Any], bool] | None = None,
    active_theme_name_getter: Callable[[], str] | None = None,
) -> None:
    parts = (line or "").strip().split()
    args = parts[1:] if parts and parts[0] == "/theme" else []

    def _active_theme_name() -> str:
        if callable(active_theme_name_getter):
            try:
                name = str(active_theme_name_getter() or "").strip().lower()
                if name:
                    return name
            except (AttributeError, TypeError, ValueError):
                pass
        return styles.get_active_theme_name()

    def _apply_theme(theme: Any) -> bool:
        if callable(theme_applier):
            failed = False
            try:
                failed = not bool(theme_applier(theme))
            except (AttributeError, TypeError, ValueError, RuntimeError):
                failed = True
            if failed:
                print(
                    styles.style(
                        styles.StyleToken.ERROR,
                        "Theme switch failed; restart to apply.",
                    )
                )
                return False
        styles.set_active_theme(theme)
        return True

    if not args:
        info = styles.get_theme_info()
        active = _active_theme_name()
        print("=== Chat Theme Settings ===")
        print(f"  Active Theme: {active}")
        print(f"  Color Mode: {info['color_mode']}")
        print(f"  Color Enabled: {info['color_enabled']}")
        print(f"  Is TTY: {info['is_tty']}")
        print(f"  NO_COLOR env: {info['no_color_env']}")
        print(f"  OPENMINION_COLOR env: {info['openminion_color_env'] or '(not set)'}")
        if data_root is not None:
            persisted = read_persisted_theme(Path(str(data_root)))
            print(f"  Persisted: {persisted or '(none)'}")
        print(
            "  Use `/theme list`, `/theme <name>`, "
            "`/theme save <name>`, or `/theme reset`."
        )
        return

    sub = args[0].lower()

    if sub == "list":
        print("Available themes:")
        active = _active_theme_name()
        for name in available_theme_names():
            marker = " (active)" if name == active else ""
            print(f"  {name}{marker}")
        return

    if sub == "reset":
        env_theme = resolve_theme(
            cli_flag=None,
            session_override=None,
            data_root=Path(str(data_root)) if data_root is not None else None,
        )
        if not _apply_theme(env_theme):
            return
        print(f"theme reset to {env_theme.name!r} (lower-precedence layer).")
        return

    if sub == "save":
        if len(args) < 2:
            print("usage: /theme save <name>")
            return
        name = args[1].strip().lower()
        if data_root is None:
            print(
                styles.style(
                    styles.StyleToken.ERROR,
                    "cannot save theme: data_root is unavailable in this context.",
                )
            )
            return
        try:
            written = write_persisted_theme(Path(str(data_root)), name)
        except ValueError as exc:
            print(styles.style(styles.StyleToken.ERROR, str(exc)))
            return
        theme = lookup_theme(name) or DARK
        if not _apply_theme(theme):
            return
        print(f"theme saved to {written}")
        print(f"active theme is now {theme.name!r}.")
        return

    name = sub
    theme = lookup_theme(name)
    if theme is None:
        valid = ", ".join(available_theme_names())
        print(
            styles.style(
                styles.StyleToken.ERROR,
                f"unknown theme {name!r}; available: {valid}",
            )
        )
        return
    if not _apply_theme(theme):
        return
    if data_root is not None:
        persist_path = persisted_theme_path(Path(str(data_root)))
        print(
            f"active theme is now {theme.name!r} (session-local). "
            f"Use `/theme save {name}` to persist to {persist_path}."
        )
    else:
        print(f"active theme is now {theme.name!r} (session-local).")
