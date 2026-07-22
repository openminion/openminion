from dataclasses import dataclass
from typing import Any, Optional
from collections.abc import Mapping, Sequence

from openminion.modules.tool.contracts import (
    normalize_raw_model_tool_name,
)
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_BROWSER,
    RUNTIME_EXEC_KILL,
    RUNTIME_EXEC_POLL,
    RUNTIME_EXEC_RUN,
    RUNTIME_TIME_NOW,
    RUNTIME_WEATHER_CURRENT,
    RUNTIME_WEB_FETCH,
    RUNTIME_WEB_SEARCH,
)
from openminion.modules.tool.runtime.manager import (
    ToolRegistryManager,
    build_default_tool_registry_manager,
)

_REGISTRY_MANAGER: Optional[ToolRegistryManager] = None
_REGISTRY: Any | None = None


def get_registry_manager() -> ToolRegistryManager:
    """Get the active registry manager, lazily initializing default if needed.

    Public getter for active wired ToolRegistryManager.
    Import-safe for registry/tool schema surfaces.
    """
    global _REGISTRY_MANAGER
    if _REGISTRY_MANAGER is None:
        _REGISTRY_MANAGER = build_default_tool_registry_manager()
    return _REGISTRY_MANAGER


def _get_registry_manager() -> ToolRegistryManager:
    """Internal alias for get_registry_manager(). Deprecated, use public getter."""
    return get_registry_manager()


def set_registry_manager(manager: ToolRegistryManager) -> None:
    """Set the global registry manager (called by bootstrap)."""
    global _REGISTRY_MANAGER
    _REGISTRY_MANAGER = manager


def get_registry() -> Any | None:
    """Return the active runtime registry when bootstrap wired one."""
    return _REGISTRY


def set_registry(registry: Any | None) -> None:
    """Set the active runtime registry (called by bootstrap)."""
    global _REGISTRY
    _REGISTRY = registry


@dataclass(frozen=True)
class BindingResolution:
    raw_tool_name: str
    model_tool_id: str
    runtime_binding_id: str
    runtime_tool_name: str
    runtime_fallback_chain: tuple[str, ...]
    source: str


def resolve_binding_for_call(
    *,
    raw_tool_name: str,
    available_tool_names: Sequence[str],
    manager: Optional[ToolRegistryManager] = None,
    allow_runtime_direct: bool = True,
) -> BindingResolution | None:
    """Resolve binding for a tool call."""
    mgr = manager if manager is not None else _get_registry_manager()
    available = {
        str(item).strip() for item in available_tool_names if str(item).strip()
    }
    raw = str(raw_tool_name or "").strip()
    if not raw:
        return None

    model_tool_id = mgr.normalize_raw_name(raw)
    if not model_tool_id and manager is None:
        # Registry.execute_calls can be exercised before runtime bootstrap has
        try:
            from openminion.modules.tool.bootstrap import (
                wire_default_tool_registry_manager,
            )

            wire_default_tool_registry_manager()
            mgr = _get_registry_manager()
            model_tool_id = mgr.normalize_raw_name(raw)
        except Exception:
            pass

    # Model-origin lanes must call canonical model_tool_id names only.
    if not allow_runtime_direct:
        canonical_raw = normalize_raw_model_tool_name(raw)
        if not canonical_raw:
            return None
        if model_tool_id and model_tool_id != canonical_raw:
            return None
        model_tool_id = canonical_raw

    # Explicit runtime tool calls remain valid only for explicitly allowed internal lanes.
    if allow_runtime_direct and raw in available:
        runtime_binding_id = mgr.resolve_binding(model_tool_id or raw) or ""
        return BindingResolution(
            raw_tool_name=raw_tool_name,
            model_tool_id=model_tool_id or raw,
            runtime_binding_id=runtime_binding_id,
            runtime_tool_name=raw,
            runtime_fallback_chain=(raw,),
            source="runtime_direct",
        )

    if not model_tool_id:
        return None

    runtime_binding_id = mgr.resolve_binding(model_tool_id)
    if not runtime_binding_id:
        return None

    candidate_chain = tuple(
        candidate
        for candidate in mgr.runtime_candidates(runtime_binding_id)
        if candidate in available
    )
    if not candidate_chain:
        return BindingResolution(
            raw_tool_name=raw_tool_name,
            model_tool_id=model_tool_id,
            runtime_binding_id=runtime_binding_id,
            runtime_tool_name="",
            runtime_fallback_chain=(),
            source="binding_unavailable",
        )

    return BindingResolution(
        raw_tool_name=raw_tool_name,
        model_tool_id=model_tool_id,
        runtime_binding_id=runtime_binding_id,
        runtime_tool_name=candidate_chain[0],
        runtime_fallback_chain=candidate_chain,
        source="model_dispatch",
    )


def adapt_arguments_for_runtime_call(
    *,
    model_tool_id: str,
    runtime_binding_id: str,
    runtime_tool_name: str,
    arguments: Mapping[str, Any] | None,
) -> dict[str, Any]:
    args = dict(arguments or {})

    if runtime_binding_id.startswith("runtime.file."):
        path = _first_non_empty(args, "path", "file_path")
        if path:
            args["path"] = path
        if runtime_binding_id.endswith(".find"):
            pattern = _first_non_empty(args, "pattern", "name")
            if pattern:
                args["pattern"] = pattern
        return args

    if runtime_binding_id == RUNTIME_EXEC_RUN:
        command = _first_non_empty(args, "command", "cmd")
        if command is not None:
            args["command"] = command
        return args

    if runtime_binding_id in {RUNTIME_EXEC_POLL, RUNTIME_EXEC_KILL}:
        session_id = _first_non_empty(args, "session_id", "id")
        if session_id is not None:
            args["session_id"] = session_id
        return args

    if runtime_binding_id == RUNTIME_WEB_SEARCH:
        query = _first_non_empty(args, "query", "q", "search_query", "text", "keywords")
        if query is not None:
            args["query"] = str(query)
        return args

    if runtime_binding_id == RUNTIME_WEB_FETCH:
        url = _first_non_empty(args, "url", "uri", "link")
        if url is not None:
            args["url"] = str(url)
        if runtime_tool_name == "fetch.head" and "method" not in args:
            args["method"] = "HEAD"
        return args

    if runtime_binding_id == RUNTIME_WEATHER_CURRENT:
        location = _first_non_empty(args, "location", "city", "query", "place")
        if location is not None:
            args["location"] = str(location)
        return args

    if runtime_binding_id == RUNTIME_TIME_NOW:
        timezone = _first_non_empty(args, "timezone", "tz")
        if timezone is not None:
            args["timezone"] = str(timezone)
        return args

    if runtime_binding_id.startswith(f"{RUNTIME_BROWSER}."):
        if runtime_tool_name == "browser":
            return _adapt_browser_unified_runtime_args(
                runtime_binding_id=runtime_binding_id,
                arguments=args,
            )

        # Provider-specific browser tools.
        if runtime_binding_id.endswith(".tab.close"):
            tab_id = _first_non_empty(args, "tab_id", "id")
            if tab_id is not None:
                args["tab_id"] = tab_id
        if runtime_binding_id.endswith(".instance.stop"):
            instance_id = _first_non_empty(args, "instance_id", "id")
            if instance_id is not None:
                args["instance_id"] = instance_id
        return args

    return args


def _adapt_browser_unified_runtime_args(
    *,
    runtime_binding_id: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    args = dict(arguments or {})
    if "op" not in args:
        op = _browser_op_for_binding(runtime_binding_id=runtime_binding_id)
        if op:
            args["op"] = op

    if runtime_binding_id.endswith(".tab.open"):
        if "instance_id" not in args:
            instance_id = _first_non_empty(args, "id")
            if instance_id:
                args["instance_id"] = instance_id
    elif runtime_binding_id.endswith(".tab.close"):
        tab_id = _first_non_empty(args, "tab_id", "id")
        if tab_id:
            args["tab_id"] = tab_id
    elif runtime_binding_id.endswith(".instance.stop"):
        instance_id = _first_non_empty(args, "instance_id", "id")
        if instance_id:
            args["instance_id"] = instance_id
    return args


def _browser_op_for_binding(*, runtime_binding_id: str) -> str:
    table = {
        f"{RUNTIME_BROWSER}.screenshot": "tab.screenshot",
        f"{RUNTIME_BROWSER}.snapshot": "tab.snapshot",
        f"{RUNTIME_BROWSER}.text": "tab.text",
        f"{RUNTIME_BROWSER}.pdf": "tab.pdf",
        f"{RUNTIME_BROWSER}.tab.open": "tab.new",
        f"{RUNTIME_BROWSER}.tab.list": "tab.list",
        f"{RUNTIME_BROWSER}.tab.close": "tab.close",
        f"{RUNTIME_BROWSER}.instance.start": "instance.start",
        f"{RUNTIME_BROWSER}.instance.stop": "instance.stop",
        f"{RUNTIME_BROWSER}.health": "daemon.ensure",
    }
    return table.get(runtime_binding_id, "")


def _first_non_empty(arguments: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key not in arguments:
            continue
        value = arguments.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            token = value.strip()
            if token:
                return token
            continue
        return value
    return None
