from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..tools.parser import normalize_tool_name_for_brain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def _tool_name_from_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("name", "") or "").strip()
    return str(getattr(item, "name", item) or "").strip()


def _schema_entries(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [
        dict(item)
        for item in items
        if isinstance(item, dict) and str(item.get("name", "") or "").strip()
    ]


def _extend_names_from_registry_source(names: set[str], source: Any) -> None:
    if isinstance(source, dict):
        candidates = source.keys()
    elif isinstance(source, (list, tuple)):
        candidates = source
    else:
        return
    for item in candidates:
        tool_name = _tool_name_from_item(item)
        if tool_name:
            names.add(tool_name)


@dataclass(slots=True)
class RunnerToolCatalog:
    """Concrete ``ToolCatalog`` backed by a live ``BrainRunner``."""

    runner: "BrainRunner"

    def list_tool_names(self) -> set[str]:
        names: set[str] = set()
        collector = getattr(self.runner, "_collect_runtime_tool_schemas", None)
        if callable(collector):
            try:
                for item in _schema_entries(collector() or []):
                    tool_name = str(item.get("name", "") or "").strip()
                    if tool_name:
                        names.add(tool_name)
            except Exception:
                pass
        tool_api = getattr(self.runner, "tool_api", None)
        if tool_api is not None and hasattr(tool_api, "list_tools"):
            try:
                raw_tools = tool_api.list_tools()
            except Exception:
                raw_tools = []
            if isinstance(raw_tools, list):
                for item in raw_tools:
                    tool_name = _tool_name_from_item(item)
                    if tool_name:
                        names.add(tool_name)
        registry = getattr(tool_api, "registry", None) if tool_api else None
        if registry is not None:
            for source in (
                getattr(registry, "_tools", None),
                getattr(registry, "tools", None),
            ):
                _extend_names_from_registry_source(names, source)
            list_fn = getattr(registry, "list", None)
            if callable(list_fn):
                try:
                    listed = list_fn()
                except Exception:
                    listed = None
                _extend_names_from_registry_source(names, listed)
        return names

    def list_tool_schemas(self) -> list[dict[str, Any]]:
        collector = getattr(self.runner, "_collect_runtime_tool_schemas", None)
        if callable(collector):
            try:
                payload = collector() or []
            except Exception:
                payload = []
            return _schema_entries(payload)
        tool_api = getattr(self.runner, "tool_api", None)
        if tool_api is not None and hasattr(tool_api, "list_tools"):
            try:
                raw_tools = tool_api.list_tools() or []
            except Exception:
                raw_tools = []
            return _schema_entries(raw_tools)
        return []

    def get_tool_schema(self, name: str) -> dict[str, Any] | None:
        token = str(name or "").strip()
        if not token:
            return None
        normalized = normalize_tool_name_for_brain(token) or token
        candidates = {token, normalized}
        for entry in self.list_tool_schemas():
            entry_name = str(entry.get("name", "") or "").strip()
            if entry_name and entry_name in candidates:
                return entry
        tool_api = getattr(self.runner, "tool_api", None)
        registry = getattr(tool_api, "registry", None) if tool_api else None
        getter = getattr(registry, "get", None) if registry else None
        if callable(getter):
            for candidate in candidates:
                try:
                    entry = getter(candidate)
                except Exception:
                    entry = None
                if isinstance(entry, dict) and str(entry.get("name", "") or "").strip():
                    return dict(entry)
                if entry is not None:
                    entry_name = str(getattr(entry, "name", "") or "").strip()
                    if entry_name:
                        return {
                            "name": entry_name,
                            "parameters": getattr(entry, "parameters", None),
                        }
        return None


__all__ = ["RunnerToolCatalog"]
