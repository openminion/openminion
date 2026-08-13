from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from openminion.modules.tool.base import Tool, ToolCategoryInfo
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.blast_radius import (
    SandboxKind,
    ToolBlastRadius,
)
from openminion.modules.tool.runtime.registry_categories import (
    DEFAULT_TOOL_CATEGORY_MAP as _DEFAULT_TOOL_CATEGORY_MAP,
    heuristic_category_for_tool_name as _heuristic_category_for_tool_name,
    mapped_category_for_tool_name as _mapped_category_for_tool_name,
    normalize_category_info as _normalize_category_info,
)

if TYPE_CHECKING:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.modules.tool.runtime import RuntimeContext


Handler = Callable[[dict[str, Any], "RuntimeContext"], dict[str, Any]]
Scope = Literal["READ_ONLY", "WRITE_SAFE", "POWER_USER", "UI_AUTOMATION"]


@dataclass
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    min_scope: Scope
    handler: Handler
    dangerous: bool = False
    idempotent: bool = True
    tags: tuple[str, ...] = ("core",)
    capabilities: tuple[str, ...] | None = None
    sidecar: str | None = None
    parameters_schema: dict[str, Any] | None = None
    prompt_visible_runtime_name: bool = False
    runtime_binding_id: str = ""
    block_under_readonly: bool = False
    blast_radius: ToolBlastRadius | None = None
    sandbox_kind: SandboxKind | None = None

    def resolved_capabilities(self) -> tuple[str, ...]:
        return self.capabilities or self.tags


@dataclass
class ToolCategoryEntry:
    tool_name: str
    primary_category: str
    secondary_categories: list[str]


@dataclass(frozen=True)
class ToolPolicyProfile:
    tool_name: str
    required_scopes_all: frozenset[str]
    risk: str
    budget_cost: int


def _wrap_runtime_handler(handler: Handler) -> Handler:
    if getattr(handler, "__openminion_runtime_wrapped__", False):
        return handler

    def wrapped_handler(*args, **kwargs):
        return handler(*args, **kwargs)

    wrapped_handler.__openminion_runtime_wrapped__ = True
    wrapped_handler.__wrapped__ = handler
    return wrapped_handler


def index_tool_category(registry: "ToolRegistry", tool_name: str, tool: Tool) -> None:
    """Index a tool into the registry's category index by inspecting its category_info."""
    try:
        info = tool.category_info()
    except Exception:
        info = ToolCategoryInfo()
    normalized = _normalize_category_info(tool_name, info)
    primary = normalized.primary_category or "general_assistance"
    registry._category_index.setdefault(primary, set()).add(tool_name)
    for secondary in normalized.secondary_categories:
        registry._category_index.setdefault(secondary, set()).add(tool_name)


def tools_by_category(registry: "ToolRegistry", category: str) -> list[str]:
    names = set(registry._category_index.get(category, set()))
    if not names:
        for tool_name in registry._tools:
            mapped = _mapped_category_for_tool_name(tool_name)
            if mapped is None:
                continue
            if (
                mapped.primary_category == category
                or category in mapped.secondary_categories
            ):
                names.add(tool_name)
    return sorted(names)


def category_for_tool(registry: "ToolRegistry", tool_name: str) -> ToolCategoryEntry:
    mapped = _mapped_category_for_tool_name(tool_name)
    if mapped is not None:
        normalized_mapped = _normalize_category_info(tool_name, mapped)
        return ToolCategoryEntry(
            tool_name=tool_name,
            primary_category=normalized_mapped.primary_category or "general_assistance",
            secondary_categories=list(normalized_mapped.secondary_categories),
        )
    tool = registry._tools.get(tool_name)
    if tool is None:
        inferred = _heuristic_category_for_tool_name(tool_name)
        return ToolCategoryEntry(
            tool_name=tool_name,
            primary_category=inferred.primary_category or "general_assistance",
            secondary_categories=list(inferred.secondary_categories),
        )
    info = ToolCategoryInfo()
    try:
        info = tool.category_info()
    except Exception:
        primary = str(getattr(tool, "primary_category", "") or "").strip()
        if primary.lower() in {"uncategorized", "general_assistance"}:
            primary = ""
        secondary_raw = getattr(tool, "secondary_categories", ()) or ()
        secondary = [
            str(item).strip()
            for item in (
                secondary_raw if isinstance(secondary_raw, (list, tuple, set)) else ()
            )
            if str(item).strip()
        ]
        if primary:
            info = ToolCategoryInfo(
                primary_category=primary, secondary_categories=tuple(secondary)
            )
        else:
            inferred = infer_categories_from_index(registry, tool_name)
            info = inferred
    info = _normalize_category_info(tool_name, info)
    return ToolCategoryEntry(
        tool_name=tool_name,
        primary_category=info.primary_category or "general_assistance",
        secondary_categories=list(info.secondary_categories),
    )


def all_categories(registry: "ToolRegistry") -> list[str]:
    return sorted(registry._category_index)


def infer_categories_from_index(
    registry: "ToolRegistry", tool_name: str
) -> ToolCategoryInfo:
    """Infer category info from the registry's category index plus fallbacks."""
    categories = [
        category
        for category, names in registry._category_index.items()
        if tool_name in names
    ]
    if not categories:
        mapped = _DEFAULT_TOOL_CATEGORY_MAP.get(tool_name)
        if mapped is not None:
            return mapped
        return _heuristic_category_for_tool_name(tool_name)

    primary = ""
    ordered = sorted(categories)
    if ordered:
        primary = ordered[0]
    secondary = [item for item in ordered if item != primary]
    return ToolCategoryInfo(
        primary_category=primary or "general_assistance",
        secondary_categories=tuple(secondary),
    )


def register_tool(registry: "ToolRegistry", tool: Any) -> None:
    key = str(tool.name).strip()
    if not key:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Tool name cannot be empty",
        )
    if isinstance(tool, ToolSpec):
        if tool.name in registry._tools:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Tool already registered: {tool.name}",
                {"tool": tool.name},
            )
        tool.handler = _wrap_runtime_handler(tool.handler)
    registry._tools[key] = tool
    index_tool_category(registry, key, tool)


def unregister_tool(registry: "ToolRegistry", tool_name: str) -> None:
    key = str(tool_name or "").strip()
    if not key:
        return
    registry._tools.pop(key, None)
    for names in registry._category_index.values():
        names.discard(key)


def add_tool_spec(registry: "ToolRegistry", spec: Any) -> None:
    if isinstance(spec, ToolSpec):
        if spec.name in registry._tools:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                f"Tool already registered: {spec.name}",
                {"tool": spec.name},
            )
        spec.handler = _wrap_runtime_handler(spec.handler)
        registry._tools[spec.name] = spec
        primary = str(getattr(spec, "primary_category", "") or "").strip()
        if primary.lower() in {"uncategorized", "general_assistance"}:
            primary = ""
        secondary = getattr(spec, "secondary_categories", []) or []
        if not primary:
            mapped = _DEFAULT_TOOL_CATEGORY_MAP.get(spec.name)
            if mapped is not None:
                primary = mapped.primary_category
                secondary = list(mapped.secondary_categories)
        if not primary:
            fallback = _heuristic_category_for_tool_name(spec.name)
            primary = fallback.primary_category or "general_assistance"
            secondary = list(fallback.secondary_categories)
        registry._category_index.setdefault(primary, set()).add(spec.name)
        for cat in secondary:
            registry._category_index.setdefault(cat, set()).add(spec.name)
        return
    raise TypeError(  # allow-bare-raise: defensive type guard on add() payload
        f"Unsupported add() payload: {type(spec).__name__}; expected ToolSpec"
    )


def list_by_capability(registry: "ToolRegistry", capability: str) -> list[ToolSpec]:
    needle = str(capability or "").strip()
    if not needle:
        return []
    matches: list[ToolSpec] = []
    for tool in registry._tools.values():
        if not isinstance(tool, ToolSpec):
            continue
        if needle in tool.resolved_capabilities():
            matches.append(tool)
    return matches
