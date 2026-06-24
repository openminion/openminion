from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CLI_DIR_NAME = "cli"
SAVED_VIEWS_FILENAME = "views.json"


@dataclass(frozen=True)
class SavedThirdBrainView:
    view_id: str
    name: str
    mode: str
    query: str
    target: str
    providers: tuple[str, ...]
    source_entity_id: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.view_id,
            "name": self.name,
            "mode": self.mode,
            "query": self.query,
            "target": self.target,
            "providers": list(self.providers),
            "source_entity_id": self.source_entity_id,
            "created_at": self.created_at,
        }


def saved_views_path(data_root: Path) -> Path:
    return Path(data_root) / _CLI_DIR_NAME / SAVED_VIEWS_FILENAME


def read_saved_views(data_root: Path) -> list[SavedThirdBrainView]:
    path = saved_views_path(data_root)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw_views = payload.get("saved_views")
    if not isinstance(raw_views, list):
        return []
    views: list[SavedThirdBrainView] = []
    for raw_view in raw_views:
        parsed = _parse_saved_view(raw_view)
        if parsed is not None:
            views.append(parsed)
    return views


def write_saved_views(data_root: Path, views: list[SavedThirdBrainView]) -> Path:
    path = saved_views_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"saved_views": [view.to_dict() for view in views]}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _parse_saved_view(raw_view: object) -> SavedThirdBrainView | None:
    if not isinstance(raw_view, dict):
        return None
    view_id = str(raw_view.get("id", "") or "").strip()
    name = str(raw_view.get("name", "") or "").strip()
    mode = str(raw_view.get("mode", "") or "").strip()
    query = str(raw_view.get("query", "") or "").strip()
    target = str(raw_view.get("target", "") or "").strip()
    source_entity_id = str(raw_view.get("source_entity_id", "") or "").strip()
    created_at = str(raw_view.get("created_at", "") or "").strip()
    raw_providers = raw_view.get("providers", [])
    if not all([view_id, name, mode, created_at]):
        return None
    if mode not in {"query", "neighborhood", "path"}:
        return None
    if not isinstance(raw_providers, list):
        return None
    providers = tuple(
        value
        for value in (str(provider or "").strip() for provider in raw_providers)
        if value
    )
    return SavedThirdBrainView(
        view_id=view_id,
        name=name,
        mode=mode,
        query=query,
        target=target,
        providers=providers,
        source_entity_id=source_entity_id,
        created_at=created_at,
    )


__all__ = [
    "SAVED_VIEWS_FILENAME",
    "SavedThirdBrainView",
    "read_saved_views",
    "saved_views_path",
    "write_saved_views",
]
