from typing import TYPE_CHECKING, Any

from openminion.modules.tool.runtime.argument_repair import (
    tool_family_for_argument_repair,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_BROWSER,
    MODEL_EXEC_RUN,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_HOST_METRICS,
    MODEL_LOCATION,
    MODEL_TIME,
    MODEL_WEATHER,
    MODEL_WEB_FETCH,
    MODEL_WEB_SEARCH,
)

from ..schemas import ToolCommand, WorkingState
from ..tool_catalog import RunnerToolCatalog
from ..tools.parser import normalize_tool_name_for_brain
from .delegation import _runner_delegate
from .validation import _build_forced_tool_guard

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def resolve_forced_tool_command(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str,
    forced_tools: list[str] | None,
    capability_category: str | None,
) -> tuple[ToolCommand | None, str | None, Any | None]:
    tool_name, status = resolve_forced_tool_name(
        runner,
        forced_tools=forced_tools,
        capability_category=capability_category,
    )
    if status in {"forced_tool_unavailable", "capability_tool_unavailable"}:
        return None, status, None
    if not tool_name:
        return None, None, None
    command = build_forced_tool_command(
        runner,
        state=state,
        user_input=user_input,
        tool_name=tool_name,
    )
    if command is None:
        return (
            None,
            "forced_tool_missing_args",
            _build_forced_tool_guard(tool_name=tool_name),
        )
    return command, None, None


def resolve_forced_tool_name(
    runner: "BrainRunner",
    *,
    forced_tools: list[str] | None,
    capability_category: str | None,
) -> tuple[str | None, str | None]:
    available_tools = available_tool_names(runner)
    if forced_tools:
        candidates: list[str] = []
        for name in forced_tools:
            token = str(name or "").strip()
            if not token:
                continue
            normalized = normalize_tool_name_for_brain(token)
            if normalized:
                candidates.append(normalized)
            elif token in available_tools:
                candidates.append(token)
        if not candidates or not available_tools:
            return None, "forced_tool_unavailable"
        for name in candidates:
            if name in available_tools:
                return name, None
        return None, "forced_tool_unavailable"
    category = str(capability_category or "").strip().lower()
    if not category or category == "none":
        return None, None

    preferred = resolve_capability_tool_fallback(
        category=category,
        available_tools=available_tools,
    )
    if preferred:
        return preferred, None

    registry = getattr(runner.tool_api, "registry", None) if runner.tool_api else None
    if registry is not None and hasattr(registry, "tools_by_category"):
        try:
            tools = registry.tools_by_category(category)
        except Exception:
            tools = []
        if tools:
            return str(tools[0]).strip() or None, None

    return None, "capability_tool_unavailable"


def resolve_capability_tool_fallback(
    *,
    category: str,
    available_tools: set[str],
) -> str | None:
    normalized = str(category or "").strip().lower()
    available = {
        str(item or "").strip() for item in available_tools if str(item or "").strip()
    }

    if not available:
        return None

    preferred_by_category: dict[str, list[str]] = {
        MODEL_FILE_LIST_DIR: [MODEL_FILE_LIST_DIR, MODEL_FILE_FIND],
        MODEL_FILE_READ: [MODEL_FILE_READ],
        MODEL_FILE_FIND: [MODEL_FILE_FIND],
        MODEL_WEB_SEARCH: [MODEL_WEB_SEARCH],
        MODEL_WEB_FETCH: [MODEL_WEB_FETCH],
        MODEL_WEATHER: [MODEL_WEATHER],
        MODEL_TIME: [MODEL_TIME],
        MODEL_LOCATION: [MODEL_LOCATION],
        MODEL_HOST_METRICS: [MODEL_HOST_METRICS],
        MODEL_BROWSER: [MODEL_BROWSER],
        MODEL_EXEC_RUN: [MODEL_EXEC_RUN],
    }

    preferred = preferred_by_category.get(normalized, [])
    for candidate in preferred:
        if candidate in available:
            return candidate
    return None


def build_forced_tool_command(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str,
    tool_name: str,
) -> ToolCommand | None:
    normalized_tool_name = normalize_tool_name_for_brain(tool_name) or str(tool_name)
    lower_name = str(normalized_tool_name).lower()
    family = tool_family_for_argument_repair(normalized_tool_name)
    args: dict[str, Any] | None = None
    if family in {MODEL_TIME, MODEL_LOCATION, MODEL_HOST_METRICS}:
        args = {}
    elif lower_name == MODEL_EXEC_RUN:
        return None
    elif lower_name == "fetch.providers":
        args = {}
    elif lower_name == MODEL_WEB_FETCH:
        return None
    else:
        return None

    idem = _runner_delegate(
        "_idempotency_key",
        runner,
        session_id=state.session_id,
        trace_id=state.trace_id or "",
        text=user_input,
    )
    return ToolCommand(
        kind="tool",
        title=f"Tool call: {normalized_tool_name}",
        tool_name=normalized_tool_name,
        args=args,
        success_criteria={"status": "success"},
        idempotency_key=idem,
        risk_level="low",
    )


def available_tool_names(runner: "BrainRunner") -> set[str]:
    """Return the set of tool names available to the brain."""

    return RunnerToolCatalog(runner).list_tool_names()


def resolve_browser_tool(runner: "BrainRunner", *, state: WorkingState) -> str | None:
    del state
    tool_names = available_tool_names(runner)

    if any(name.startswith("browser.pinchtab") for name in tool_names):
        return "browser.pinchtab"
    if any(name.startswith("browser.playwright") for name in tool_names):
        return "browser.playwright"

    return None
