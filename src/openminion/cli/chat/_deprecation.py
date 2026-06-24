from __future__ import annotations

import sys
from typing import Any

_NOTICE_TEXT = (
    "(openminion chat is in maintenance mode — `openminion focus` "
    "is the recommended interactive surface. "
    "See the chat migration guide. "
    "Suppress with OPENMINION_CHAT_NO_DEPRECATION=1.)"
)


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _stdout_is_tty() -> bool:
    isatty = getattr(sys.stdout, "isatty", lambda: False)
    try:
        return bool(isatty())
    except (OSError, TypeError, ValueError):
        return False


def _read_suppression_env() -> str:
    from openminion.base.config.env import EnvironmentConfig

    env = EnvironmentConfig.from_sources()
    return str(env.get("OPENMINION_CHAT_NO_DEPRECATION", "") or "")


def should_print_notice() -> bool:
    if not _stdout_is_tty():
        return False
    return not _is_truthy(_read_suppression_env())


def print_deprecation_notice(*, console: Any | None = None) -> None:
    if not should_print_notice():
        return
    if console is not None:
        try:
            from rich.text import Text

            console.print(Text(_NOTICE_TEXT, style="dim italic"))
            return
        except Exception:
            pass
    print(_NOTICE_TEXT)
