from typing import Any, Mapping


def _is_mock_like(value: Any) -> bool:
    return value is not None and "unittest.mock" in type(value).__module__


def _cfg_section(config: Any, name: str) -> Any | None:
    if config is None or _is_mock_like(config):
        return None
    if isinstance(config, Mapping):
        section = config.get(name)
    else:
        section = getattr(config, name, None)
    return None if _is_mock_like(section) else section


def _cfg_value(section: Any, name: str, default: Any) -> Any:
    if section is None:
        return default
    if isinstance(section, Mapping):
        value = section.get(name, default)
    else:
        value = getattr(section, name, default)
    return default if _is_mock_like(value) else value


def _safe_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _safe_int(value: Any, default: int, *, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    return parsed


def _safe_float(
    value: Any,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


__all__ = [
    "_cfg_section",
    "_cfg_value",
    "_is_mock_like",
    "_safe_bool",
    "_safe_float",
    "_safe_int",
]
