from typing import Any, TYPE_CHECKING

from openminion.modules.tool.schema_service import ToolSchemaService
from openminion.modules.tool.dispatch import _get_registry_manager

from .parser import normalize_tool_name_for_brain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


_TOOL_SCHEMA_SERVICE = ToolSchemaService()


def _normalize_execution_tool_name(raw_name: str) -> str | None:
    token = str(raw_name or "").strip()
    if not token:
        return None
    manager = _get_registry_manager()
    return manager.normalize_raw_name(token) or normalize_tool_name_for_brain(token)


def collect_runtime_tool_schemas(runner: "BrainRunner") -> list[dict[str, Any]]:
    tool_api = getattr(runner, "tool_api", None)
    if tool_api is None or not hasattr(tool_api, "registry"):
        return []
    registry = getattr(tool_api, "registry", None)
    if registry is None:
        return []
    return _TOOL_SCHEMA_SERVICE.collect_execution_tool_schemas(
        registry=registry,
        normalize_name=_normalize_execution_tool_name,
    )


def build_prompt_tool_schemas(
    runner: "BrainRunner", *, user_input: str | None
) -> list[dict[str, Any]]:
    raw_tools = collect_runtime_tool_schemas(runner)
    return _TOOL_SCHEMA_SERVICE.build_prompt_tool_schemas(
        query=user_input or "",
        tool_schemas=raw_tools,
    )


tool_schema_stub = _TOOL_SCHEMA_SERVICE.tool_stub
trim_text = _TOOL_SCHEMA_SERVICE.trim_text
tool_description = _TOOL_SCHEMA_SERVICE.tool_description
tool_parameters = _TOOL_SCHEMA_SERVICE.tool_parameters
