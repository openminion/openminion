from __future__ import annotations

import sys
from enum import Enum

from openminion.base.config.env import resolve_environment_config
from openminion.cli.constants import (
    CLI_DEFAULT_THEME_VARIANT,
    CLI_THEME_VARIANTS,
    CLI_THEME_VERSION,
    OPENMINION_THEME_VARIANT_ENV,
)


class StyleToken(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    WARNING = "warning"
    ERROR = "error"
    MUTED = "muted"
    PROMPT = "prompt"
    SPINNER = "spinner"
    SUCCESS = "success"
    INFO = "info"


_TOKEN_TO_THEME_FIELD: dict[StyleToken, str] = {
    StyleToken.USER: "text_accent",
    StyleToken.ASSISTANT: "state_ok",
    StyleToken.SYSTEM: "text_secondary",
    StyleToken.WARNING: "state_warning",
    StyleToken.ERROR: "state_error",
    StyleToken.MUTED: "text_muted",
    StyleToken.PROMPT: "text_accent",
    StyleToken.SPINNER: "state_warning",
    StyleToken.SUCCESS: "state_ok",
    StyleToken.INFO: "text_accent",
}


def _hex_to_truecolor_ansi(hex_color: str) -> tuple[str, str]:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return ("", "")
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return (f"\033[38;2;{r};{g};{b}m", "\033[0m")


def _ansi_codes_from_theme(theme) -> dict[StyleToken, tuple[str, str]]:
    return {
        token: _hex_to_truecolor_ansi(getattr(theme, field))
        for token, field in _TOKEN_TO_THEME_FIELD.items()
    }


def _build_default_ansi_codes() -> dict[StyleToken, tuple[str, str]]:
    from openminion.cli.theme import DARK

    return _ansi_codes_from_theme(DARK)


_ANSI_CODES: dict[StyleToken, tuple[str, str]] = _build_default_ansi_codes()


_ACTIVE_THEME_NAME: str = "dark"


def set_active_theme(theme) -> None:
    """Rebuild ``_ANSI_CODES`` from a new theme."""
    global _ACTIVE_THEME_NAME
    new_codes = _ansi_codes_from_theme(theme)
    _ANSI_CODES.clear()
    _ANSI_CODES.update(new_codes)
    name = getattr(theme, "name", "")
    if isinstance(name, str) and name:
        _ACTIVE_THEME_NAME = name


def get_active_theme_name() -> str:
    """Return the active theme name (lowercase), default ``"dark"``."""
    return _ACTIVE_THEME_NAME


_COLOR_MODE: str | None = None


def _detect_color_mode() -> str:
    global _COLOR_MODE
    if _COLOR_MODE is not None:
        return _COLOR_MODE

    env_config = resolve_environment_config()
    if env_config.get("NO_COLOR", ""):
        _COLOR_MODE = "off"
        return _COLOR_MODE

    openminion_color = env_config.get("OPENMINION_COLOR", "").strip().lower()
    if openminion_color in {"1", "true", "on"}:
        _COLOR_MODE = "on"
        return _COLOR_MODE
    if openminion_color in {"0", "false", "off"}:
        _COLOR_MODE = "off"
        return _COLOR_MODE

    if sys.stdout.isatty():
        _COLOR_MODE = "auto"
    else:
        _COLOR_MODE = "off"

    return _COLOR_MODE


def get_color_mode() -> str:
    return _detect_color_mode()


def is_color_enabled() -> bool:
    mode = _detect_color_mode()
    if mode == "on":
        return True
    if mode == "off":
        return False
    return sys.stdout.isatty()


def style(token: StyleToken, text: str) -> str:
    open_code, close_code = style_token(token)
    if not open_code:
        return text
    return f"{open_code}{text}{close_code}"


def style_token(token: StyleToken) -> tuple[str, str]:
    if not is_color_enabled():
        return ("", "")
    return _ANSI_CODES.get(token, ("", ""))


def format_prefix(token: StyleToken, text: str) -> str:
    return f"{style(token, text)}: "


def clear_line() -> str:
    return "\r\033[K" if is_color_enabled() else "\r"


_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spinner_index = 0


def get_spinner_frame() -> str:
    global _spinner_index
    frame = _SPINNER_FRAMES[_spinner_index % len(_SPINNER_FRAMES)]
    _spinner_index += 1
    return style(StyleToken.SPINNER, frame)


def reset_spinner() -> None:
    global _spinner_index
    _spinner_index = 0


def get_theme_info() -> dict:
    env_config = resolve_environment_config()
    return {
        "color_mode": get_color_mode(),
        "color_enabled": is_color_enabled(),
        "is_tty": sys.stdout.isatty(),
        "no_color_env": bool(env_config.get("NO_COLOR", "")),
        "openminion_color_env": env_config.get("OPENMINION_COLOR", ""),
        "theme_version": CLI_THEME_VERSION,
        "theme_variant": _get_theme_variant(),
    }


def _get_theme_variant() -> str:
    variant = (
        resolve_environment_config()
        .get(OPENMINION_THEME_VARIANT_ENV, "")
        .strip()
        .lower()
    )
    if variant in CLI_THEME_VARIANTS:
        return variant
    return CLI_DEFAULT_THEME_VARIANT


def set_theme_variant(variant: str) -> None:
    global _THEME_VARIANT
    if variant in CLI_THEME_VARIANTS:
        _THEME_VARIANT = variant


_THEME_VARIANT: str | None = None


def get_theme_variant() -> str:
    if _THEME_VARIANT is not None:
        return _THEME_VARIANT
    return _get_theme_variant()
