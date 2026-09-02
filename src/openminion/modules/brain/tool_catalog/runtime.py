from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..tools.parser import normalize_tool_name_for_brain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def _tool_name_from_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("name", "") or "").strip()
    return str(getattr(item, "name", item) or "").strip()


def _schema_payload(item: Any) -> dict[str, Any] | None:
    if isinstance(item, dict):
        payload = dict(item)
    else:
        name = _tool_name_from_item(item)
        if not name:
            return None
        parameters = getattr(item, "parameters", None)
        payload = {
            "name": name,
            "parameters": dict(parameters)
            if isinstance(parameters, dict)
            else parameters,
        }
    return payload if _tool_name_from_item(payload) else None


def _schema_entries(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [payload for item in items if (payload := _schema_payload(item)) is not None]


def _call_optional(callback: Any, *args: Any) -> Any:
    if not callable(callback):
        return None
    try:
        return callback(*args)
    except Exception:
        return None


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

    def _is_allowed(self, tool_name: str) -> bool:
        tool_api = getattr(self.runner, "tool_api", None)
        is_allowed = getattr(tool_api, "is_tool_allowed", None)
        return not callable(is_allowed) or bool(is_allowed(tool_name))

    def list_tool_names(self) -> set[str]:
        names: set[str] = set()
        collector = getattr(self.runner, "_collect_runtime_tool_schemas", None)
        if callable(collector):
            _extend_names_from_registry_source(
                names,
                _schema_entries(_call_optional(collector) or []),
            )
        tool_api = getattr(self.runner, "tool_api", None)
        _extend_names_from_registry_source(
            names,
            _call_optional(getattr(tool_api, "list_tools", None)),
        )
        registry = getattr(tool_api, "registry", None) if tool_api else None
        if registry is not None:
            for source in (
                getattr(registry, "_tools", None),
                getattr(registry, "tools", None),
            ):
                _extend_names_from_registry_source(names, source)
            _extend_names_from_registry_source(
                names,
                _call_optional(getattr(registry, "list", None)),
            )
        return {name for name in names if self._is_allowed(name)}

    def list_tool_schemas(self) -> list[dict[str, Any]]:
        collector = getattr(self.runner, "_collect_runtime_tool_schemas", None)
        if callable(collector):
            schemas = _schema_entries(_call_optional(collector) or [])
            return [
                item for item in schemas if self._is_allowed(_tool_name_from_item(item))
            ]
        tool_api = getattr(self.runner, "tool_api", None)
        schemas = _schema_entries(
            _call_optional(getattr(tool_api, "list_tools", None)) or []
        )
        return [
            item for item in schemas if self._is_allowed(_tool_name_from_item(item))
        ]

    def get_tool_schema(self, name: str) -> dict[str, Any] | None:
        token = str(name or "").strip()
        if not token:
            return None
        normalized = normalize_tool_name_for_brain(token) or token
        candidates = {token, normalized}
        if not any(self._is_allowed(candidate) for candidate in candidates):
            return None
        for schema in self.list_tool_schemas():
            entry_name = str(schema.get("name", "") or "").strip()
            if entry_name and entry_name in candidates:
                return schema
        tool_api = getattr(self.runner, "tool_api", None)
        registry = getattr(tool_api, "registry", None) if tool_api else None
        if callable(getattr(registry, "get", None)):
            for candidate in candidates:
                payload = _schema_payload(
                    _call_optional(getattr(registry, "get", None), candidate)
                )
                if payload is not None:
                    return payload
        tools = getattr(registry, "_tools", None)
        if isinstance(tools, dict):
            for candidate in candidates:
                payload = _schema_payload(tools.get(candidate))
                if payload is not None:
                    return payload
        return None


__all__ = ["RunnerToolCatalog"]
