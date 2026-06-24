"""Logging setup, filtering, and structured event formatting."""

import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TextIO

from openminion.base.constants import (
    BASE_COLOR_FORCE_FALSE_VALUES,
    BASE_COLOR_FORCE_TRUE_VALUES,
    NO_COLOR_ENV,
    OPENMINION_COLOR_ENV,
    OPENMINION_LOG_COLOR_ENV,
    OPENMINION_LOG_LEVEL_ENV,
)


_PERIODIC_EVENT_TYPES = {
    "component.heartbeat",
    "cron.scheduler.heartbeat",
    "runtime.manager.sweep",
}
_INTERACTIVE_LEVEL_OVERRIDES: dict[str, int] = {
    "": logging.WARNING,
    "openminion": logging.WARNING,
    "openminion.gateway": logging.ERROR,
    "openminion.provider": logging.ERROR,
}
_DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_LOG_NAMESPACE = "openminion"


class _ConsolePeriodicEventFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _record_is_periodic(record)


def _record_is_periodic(record: logging.LogRecord) -> bool:
    event_type = _extract_event_type(record)
    if event_type in _PERIODIC_EVENT_TYPES:
        return True
    message = ""
    try:
        message = record.getMessage()
    except Exception:
        message = str(getattr(record, "msg", "") or "")
    if "event=component.heartbeat" in message:
        return True
    if "event=cron.scheduler.heartbeat" in message:
        return True
    if "source=cron.scheduler.heartbeat" in message:
        return True
    return False


def _extract_event_type(record: logging.LogRecord) -> str:
    event_type = str(getattr(record, "event_type", "") or "").strip()
    if event_type:
        return event_type
    args = getattr(record, "args", ())
    if isinstance(args, tuple) and args:
        first = str(args[0] or "").strip()
        if first:
            return first
    if isinstance(args, Mapping):
        mapped = str(args.get("event_type", "") or "").strip()
        if mapped:
            return mapped
    return ""


def _is_console_handler(handler: logging.Handler) -> bool:
    if not isinstance(handler, logging.StreamHandler):
        return False
    if isinstance(handler, logging.FileHandler):
        return False
    return True


def _ensure_console_periodic_filter(handler: logging.Handler) -> None:
    for existing in list(handler.filters):
        if isinstance(existing, _ConsolePeriodicEventFilter):
            return
    handler.addFilter(_ConsolePeriodicEventFilter())


def _clear_console_periodic_filter(handler: logging.Handler) -> None:
    for existing in list(handler.filters):
        if isinstance(existing, _ConsolePeriodicEventFilter):
            handler.removeFilter(existing)


def _apply_logger_level_overrides(overrides: Mapping[str, int]) -> None:
    for logger_name, logger_level in overrides.items():
        if logger_name:
            logging.getLogger(logger_name).setLevel(logger_level)
            continue
        logging.getLogger().setLevel(logger_level)


def _normalize_logger_name(name: str | None) -> str:
    normalized = str(name or "").strip()
    if not normalized:
        return _LOG_NAMESPACE
    normalized = normalized.replace("/", ".").replace("..", ".").strip(".")
    if not normalized:
        return _LOG_NAMESPACE
    if normalized == _LOG_NAMESPACE or normalized.startswith(f"{_LOG_NAMESPACE}."):
        return normalized
    return f"{_LOG_NAMESPACE}.{normalized}"


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(_normalize_logger_name(name))


def format_structured_event(event: str, /, **fields: object) -> str:
    event_name = str(event or "").strip() or "unknown"
    tokens = [f"event={event_name}"]
    for key, value in fields.items():
        token = str(key or "").strip()
        if not token or value is None:
            continue
        rendered = str(value).strip()
        if not rendered:
            continue
        rendered = rendered.replace("\n", "\\n")
        tokens.append(f"{token}={rendered}")
    return " ".join(tokens)


def apply_logging_mode(mode: str) -> None:
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode == "interactive":
        _apply_logger_level_overrides(_INTERACTIVE_LEVEL_OVERRIDES)


def _resolve_level(value: str | int | None, *, fallback: int) -> int:
    if isinstance(value, int):
        return value
    normalized = str(value or "").strip()
    if not normalized:
        return fallback
    return int(getattr(logging, normalized.upper(), fallback))


def _normalize_file_path(file_path: str | Path | None) -> Path | None:
    normalized = str(file_path or "").strip()
    if not normalized:
        return None
    return Path(normalized).expanduser().resolve()


def _find_file_handler(
    root_logger: logging.Logger, target_path: Path
) -> logging.FileHandler | None:
    target_text = str(target_path)
    for handler in root_logger.handlers:
        if not isinstance(handler, logging.FileHandler):
            continue
        current = str(getattr(handler, "baseFilename", "") or "")
        if current == target_text:
            return handler
    return None


def _ensure_file_handler(
    root_logger: logging.Logger, file_path: str | Path | None
) -> None:
    resolved_path = _normalize_file_path(file_path)
    if resolved_path is None:
        return
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _find_file_handler(root_logger, resolved_path)
    if existing is not None:
        return
    file_handler = logging.FileHandler(resolved_path, mode="a", encoding="utf-8")
    root_logger.addHandler(file_handler)


def configure_logging(
    level: str = "INFO",
    *,
    mode: str = "default",
    file_path: str | Path | None = None,
    file_level: str | int = "DEBUG",
) -> logging.Logger:
    override = str(os.environ.get(OPENMINION_LOG_LEVEL_ENV, "")).strip()
    effective = override or str(level or "INFO")
    numeric_level = _resolve_level(effective, fallback=logging.INFO)
    numeric_file_level = _resolve_level(file_level, fallback=logging.DEBUG)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=numeric_level,
            format=_DEFAULT_LOG_FORMAT,
        )
        root_logger = logging.getLogger()
    _ensure_file_handler(root_logger, file_path)

    root_logger.setLevel(numeric_level)
    formatter = _build_formatter(stream=sys.stderr)
    plain_formatter = logging.Formatter(_DEFAULT_LOG_FORMAT)
    for handler in root_logger.handlers:
        if isinstance(handler, logging.FileHandler):
            handler.setLevel(numeric_file_level)
            handler.setFormatter(plain_formatter)
            _clear_console_periodic_filter(handler)
            continue

        handler.setLevel(numeric_level)
        if formatter is not None:
            handler.setFormatter(formatter)
        if _is_console_handler(handler):
            _ensure_console_periodic_filter(handler)
        else:
            _clear_console_periodic_filter(handler)

    logger = get_logger()
    logger.setLevel(numeric_level)
    apply_logging_mode(mode)
    return logger


def _build_formatter(*, stream: TextIO) -> logging.Formatter:
    base_format = _DEFAULT_LOG_FORMAT
    colorize = _should_colorize_logs(stream=stream)
    if not colorize:
        return logging.Formatter(base_format)
    return _ColorLogFormatter(base_format)


def _should_colorize_logs(*, stream: TextIO) -> bool:
    log_color_override = (
        str(os.environ.get(OPENMINION_LOG_COLOR_ENV, "")).strip().lower()
    )
    if log_color_override in BASE_COLOR_FORCE_FALSE_VALUES:
        return False
    if log_color_override in BASE_COLOR_FORCE_TRUE_VALUES:
        return True

    if str(os.environ.get(NO_COLOR_ENV, "")).strip():
        return False

    global_color_override = (
        str(os.environ.get(OPENMINION_COLOR_ENV, "")).strip().lower()
    )
    if global_color_override in BASE_COLOR_FORCE_FALSE_VALUES:
        return False
    if global_color_override in BASE_COLOR_FORCE_TRUE_VALUES:
        return True

    return bool(getattr(stream, "isatty", lambda: False)())


class _ColorLogFormatter(logging.Formatter):
    _RESET = "\033[0m"
    _LEVEL_STYLES = {
        logging.DEBUG: "\033[2;37m",
        logging.INFO: "\033[2;37m",
        logging.WARNING: "\033[2;33m",
        logging.ERROR: "\033[2;31m",
        logging.CRITICAL: "\033[1;31m",
    }

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        style = self._LEVEL_STYLES.get(record.levelno, "\033[2;37m")
        return f"{style}{rendered}{self._RESET}"
