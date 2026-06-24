"""User/project/local settings resolution."""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


SETTINGS_DIRNAME = ".openminion"
SETTINGS_FILENAME = "settings.json"
LOCAL_SETTINGS_FILENAME = "settings.local.json"

_LOGGER = logging.getLogger(__name__)


def _merge_settings(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overlay.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _merge_settings(current, value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


@dataclass
class SettingsResolver:
    """Resolve user/project/local settings with local precedence."""

    workspace_root: Path | str | None = None
    user_home: Path | str | None = None
    logger: logging.Logger = field(default=_LOGGER)
    _settings: dict[str, Any] | None = field(default=None, init=False, repr=False)

    def source_paths(self) -> tuple[Path, Path, Path]:
        user_home = Path(self.user_home).expanduser() if self.user_home else Path.home()
        workspace = (
            Path(self.workspace_root).expanduser()
            if self.workspace_root is not None
            else Path.cwd()
        )
        return (
            user_home / SETTINGS_DIRNAME / SETTINGS_FILENAME,
            workspace / SETTINGS_DIRNAME / SETTINGS_FILENAME,
            workspace / SETTINGS_DIRNAME / LOCAL_SETTINGS_FILENAME,
        )

    def load(self) -> dict[str, Any]:
        if self._settings is not None:
            return copy.deepcopy(self._settings)

        payload: dict[str, Any] = {}
        for path in self.source_paths():
            source_payload = self._read_settings_file(path)
            if source_payload is None:
                continue
            payload = _merge_settings(payload, source_payload)
        self._settings = payload
        return copy.deepcopy(payload)

    def reload(self) -> dict[str, Any]:
        self._settings = None
        return self.load()

    def get(self, key: str, default: Any = None) -> Any:
        value = self.load().get(key, default)
        return copy.deepcopy(value)

    def lifecycle_hooks_for_event(self, event_type: str) -> list[dict[str, str]]:
        hooks = self.load().get("hooks", {})
        if not isinstance(hooks, Mapping):
            return []
        entries = hooks.get(str(event_type or "").strip(), [])
        if not isinstance(entries, list):
            return []
        normalized: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            command = str(entry.get("command", "") or "").strip()
            if not command:
                continue
            matcher = str(entry.get("matcher", "*") or "*").strip() or "*"
            normalized.append({"command": command, "matcher": matcher})
        return normalized

    def _read_settings_file(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Skipping malformed settings file %s: %s", path, exc)
            return None
        if not isinstance(raw, dict):
            self.logger.warning("Skipping non-object settings file %s", path)
            return None
        return raw


__all__ = [
    "LOCAL_SETTINGS_FILENAME",
    "SETTINGS_DIRNAME",
    "SETTINGS_FILENAME",
    "SettingsResolver",
]
