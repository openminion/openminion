from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Literal

VerbosityLevel = Literal["quiet", "normal", "verbose"]
ProgressLevel = Literal["full", "minimal", "off"]

_VERBOSITY_VALUES: tuple[str, ...] = ("quiet", "normal", "verbose")
_PROGRESS_VALUES: tuple[str, ...] = ("full", "minimal", "off")
_TRUTHY_VALUES: tuple[str, ...] = ("1", "true", "yes", "on")

_PREFS_FILE_BASENAME = "focus_prefs.toml"
_PREFS_RECOGNIZED_KEYS: tuple[str, ...] = ("verbosity", "progress")


def _read_env_value(key: str) -> str:
    """Read an env var through the centralized config owner."""
    from openminion.base.config.env import EnvironmentConfig

    env = EnvironmentConfig.from_sources()
    return str(env.get(key, "") or "")


def _emit_deprecation_warning(old: str, new: str) -> None:
    print(
        f"openminion: {old} is deprecated; use {new} instead. "
        f"Both still work, but {old} will be removed in a future release.",
        file=sys.stderr,
    )


def _resolve_preferences_file_path() -> Path:
    data_root_str = _read_env_value("OPENMINION_DATA_ROOT").strip()
    if data_root_str:
        data_root = Path(data_root_str).expanduser()
    else:
        data_root = Path.home() / ".openminion"
    return data_root / _PREFS_FILE_BASENAME


def _read_preferences_file() -> dict[str, str]:
    path = _resolve_preferences_file_path()
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(
            f"openminion: failed to read focus preferences from {path}: "
            f"{exc}. Falling back to defaults.",
            file=sys.stderr,
        )
        return {}
    result: dict[str, str] = {}
    for key in _PREFS_RECOGNIZED_KEYS:
        value = data.get(key)
        if isinstance(value, str):
            result[key] = value.strip().lower()
    return result


def _normalize_choice(value: object, allowed: tuple[str, ...]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in allowed else None


def _warn_invalid_value(
    source: str, value: str, valid_values: str, fallback: str
) -> None:
    print(
        f"openminion: unrecognized {source}={value!r}; "
        f"falling back to {fallback}. Valid values: {valid_values}.",
        file=sys.stderr,
    )


def resolve_verbosity(
    args: object, *, default: VerbosityLevel = "normal"
) -> VerbosityLevel:
    flag_value = getattr(args, "verbosity", None)
    if flag_value in _VERBOSITY_VALUES:
        return flag_value  # type: ignore[return-value]

    raw_env = _read_env_value("OPENMINION_VERBOSITY")
    normalized_env = _normalize_choice(raw_env, _VERBOSITY_VALUES)
    if normalized_env is not None:
        return normalized_env  # type: ignore[return-value]
    if raw_env:
        _warn_invalid_value(
            "OPENMINION_VERBOSITY",
            raw_env.strip().lower(),
            "quiet, normal, verbose",
            "default",
        )

    legacy_env = _read_env_value("OPENMINION_FOCUS_VERBOSITY")
    normalized_legacy = _normalize_choice(legacy_env, _VERBOSITY_VALUES)
    if normalized_legacy is not None:
        _emit_deprecation_warning("OPENMINION_FOCUS_VERBOSITY", "OPENMINION_VERBOSITY")
        return normalized_legacy  # type: ignore[return-value]
    if legacy_env:
        print(
            f"openminion: unrecognized OPENMINION_FOCUS_VERBOSITY={legacy_env.strip().lower()!r}; "
            f"falling back to 'normal'. Valid values: quiet, normal, verbose. "
            f"(Use OPENMINION_VERBOSITY going forward — "
            f"OPENMINION_FOCUS_VERBOSITY is deprecated.)",
            file=sys.stderr,
        )

    prefs = _read_preferences_file()
    prefs_value = prefs.get("verbosity", "")
    normalized_prefs = _normalize_choice(prefs_value, _VERBOSITY_VALUES)
    if normalized_prefs is not None:
        return normalized_prefs  # type: ignore[return-value]
    if prefs_value:
        _warn_invalid_value(
            "verbosity",
            prefs_value,
            "quiet, normal, verbose",
            f"default in {_resolve_preferences_file_path()}",
        )

    return default


def _stream_is_tty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", lambda: False)
    try:
        return bool(isatty())
    except (OSError, TypeError, ValueError):
        return False


def _stdin_is_tty() -> bool:
    return _stream_is_tty(sys.stdin)


def _stdout_is_tty() -> bool:
    return _stream_is_tty(sys.stdout)


def _auto_detect_progress() -> ProgressLevel:
    if _stdin_is_tty() and _stdout_is_tty():
        return "full"
    return "off"


def _resolve_progress_alias_flags(args: object) -> ProgressLevel | None:
    if bool(getattr(args, "no_progress", False)):
        return "off"
    if bool(getattr(args, "no_activity_indicator", False)):
        return "off"
    if bool(getattr(args, "plain_spinner", False)):
        return "minimal"
    return None


def resolve_progress(
    args: object, *, default: ProgressLevel | None = None
) -> ProgressLevel:
    flag_value = getattr(args, "progress", None)
    if flag_value in _PROGRESS_VALUES:
        return flag_value  # type: ignore[return-value]

    alias = _resolve_progress_alias_flags(args)
    if alias is not None:
        return alias

    raw_env = _read_env_value("OPENMINION_PROGRESS")
    normalized_env = _normalize_choice(raw_env, _PROGRESS_VALUES)
    if normalized_env is not None:
        return normalized_env  # type: ignore[return-value]
    if raw_env:
        _warn_invalid_value(
            "OPENMINION_PROGRESS",
            raw_env.strip().lower(),
            "full, minimal, off",
            "default",
        )

    legacy_plain = _read_env_value("OPENMINION_FOCUS_PLAIN_SPINNER").strip().lower()
    if legacy_plain in _TRUTHY_VALUES:
        _emit_deprecation_warning(
            "OPENMINION_FOCUS_PLAIN_SPINNER", "OPENMINION_PROGRESS=minimal"
        )
        return "minimal"

    no_color = _read_env_value("NO_COLOR").strip()
    if no_color:
        return "minimal"

    prefs = _read_preferences_file()
    prefs_value = prefs.get("progress", "")
    normalized_prefs = _normalize_choice(prefs_value, _PROGRESS_VALUES)
    if normalized_prefs is not None:
        return normalized_prefs  # type: ignore[return-value]
    if prefs_value:
        _warn_invalid_value(
            "progress",
            prefs_value,
            "full, minimal, off",
            f"default in {_resolve_preferences_file_path()}",
        )

    if default is not None:
        return default

    return _auto_detect_progress()


def add_verbosity_flag(parser: argparse.ArgumentParser) -> None:
    """Register the canonical `--verbosity` flag."""
    parser.add_argument(
        "--verbosity",
        choices=list(_VERBOSITY_VALUES),
        default=None,
        help=(
            "Tool-block rendering verbosity. quiet hides tool blocks "
            "(end-of-turn summary still shown); normal truncates to "
            "6 lines with /expand affordance (default); verbose shows "
            "full output up to a 200-line cap. Same effect as "
            "OPENMINION_VERBOSITY=<level>. "
            "OPENMINION_FOCUS_VERBOSITY (legacy) still honored with "
            "deprecation warning."
        ),
    )


def add_progress_flag(
    parser: argparse.ArgumentParser, *, include_aliases: bool = True
) -> None:
    """Register the canonical `--progress` flag and optional aliases."""
    parser.add_argument(
        "--progress",
        choices=list(_PROGRESS_VALUES),
        default=None,
        help=(
            "In-flight chrome level. full shows phase spinner + "
            "elapsed counter (default on TTY); minimal drops verb "
            "rotation but keeps elapsed; off suppresses all chrome. "
            "Auto-detects to off when stdin or stdout is piped. "
            "Same effect as OPENMINION_PROGRESS=<level>."
        ),
    )
    if include_aliases:
        parser.add_argument(
            "--no-progress",
            action="store_true",
            help="Legacy alias for --progress off.",
        )
        parser.add_argument(
            "--no-activity-indicator",
            action="store_true",
            help="Legacy alias for --progress off.",
        )
        parser.add_argument(
            "--plain-spinner",
            action="store_true",
            help=(
                "Legacy alias for --progress minimal. Drops verb "
                "rotation but keeps the elapsed counter."
            ),
        )
