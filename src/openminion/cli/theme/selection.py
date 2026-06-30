import json
from pathlib import Path

from openminion.base.config.env import resolve_environment_config
from openminion.cli.constants import OPENMINION_THEME_VARIANT_ENV

from .catalog import DARK, SHIPPED_THEMES
from .models import Theme

__all__ = [
    "PERSISTED_THEME_FILENAME",
    "available_theme_names",
    "lookup_theme",
    "persisted_theme_path",
    "read_persisted_theme",
    "resolve_theme",
    "write_persisted_theme",
]


PERSISTED_THEME_FILENAME = "theme.json"
_THEME_DIR_NAME = "cli"


def available_theme_names() -> list[str]:
    return sorted(SHIPPED_THEMES.keys())


def lookup_theme(name: str | None) -> Theme | None:
    if not name:
        return None
    key = name.strip().lower()
    return SHIPPED_THEMES.get(key)


def persisted_theme_path(data_root: Path) -> Path:
    return Path(data_root) / _THEME_DIR_NAME / PERSISTED_THEME_FILENAME


def read_persisted_theme(data_root: Path) -> str | None:
    path = persisted_theme_path(data_root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("theme")
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def write_persisted_theme(data_root: Path, name: str) -> Path:
    if lookup_theme(name) is None:
        valid = ", ".join(available_theme_names())
        raise ValueError(f"unknown theme {name!r}; available: {valid}")
    path = persisted_theme_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"theme": name.strip().lower()}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_theme(
    *,
    cli_flag: str | None = None,
    session_override: str | None = None,
    data_root: Path | None = None,
    env_value: str | None = None,
) -> Theme:
    resolved_env_value = env_value
    if resolved_env_value is None:
        resolved_env_value = (
            resolve_environment_config()
            .get(OPENMINION_THEME_VARIANT_ENV, "")
            .strip()
            .lower()
            or None
        )
    candidates: list[str | None] = [
        cli_flag,
        session_override,
        resolved_env_value,
    ]
    if data_root is not None:
        candidates.append(read_persisted_theme(data_root))

    for candidate in candidates:
        theme = lookup_theme(candidate)
        if theme is not None:
            return theme
    return DARK
