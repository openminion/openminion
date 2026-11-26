from copy import deepcopy
from typing import Any, TYPE_CHECKING

from openminion.modules.task.project.policy import security_repair_delegation_modes
from openminion.modules.tool.contracts.model_ids import MODEL_TASK_DELEGATE
from openminion.modules.tool.schema_service import ToolSchemaService
from openminion.modules.tool.registry.runtime import binding_manager

from .parser import normalize_tool_name_for_brain

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner
    from ..schemas.state.working import WorkingState


_TOOL_SCHEMA_SERVICE = ToolSchemaService()


def runtime_tool_scope(state: "WorkingState") -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "project_task_id": state.resume_task_id_hint,
    }


def _normalize_execution_tool_name(
    raw_name: str,
    *,
    registry: Any | None = None,
) -> str | None:
    token = str(raw_name or "").strip()
    if not token:
        return None
    manager = binding_manager(registry)
    return manager.normalize_raw_name(token) or normalize_tool_name_for_brain(
        token,
        registry=registry,
    )


def collect_runtime_tool_schemas(
    runner: "BrainRunner", *, metadata: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    tool_api = getattr(runner, "tool_api", None)
    registry = getattr(tool_api, "registry", None)
    if registry is None:
        return []
    metadata = dict(metadata or {})
    context_builder = getattr(tool_api, "_build_execution_context_builder", None)
    if context_builder is not None:
        context_builder().project_security_lab(
            metadata, str(metadata.get("session_id", "") or "")
        )
    schemas = _TOOL_SCHEMA_SERVICE.collect_execution_tool_schemas(
        registry=registry,
        normalize_name=lambda raw_name: _normalize_execution_tool_name(
            raw_name,
            registry=registry,
        ),
        metadata=metadata,
    )
    modes = security_repair_delegation_modes(
        getattr(tool_api, "task_manager", None),
        task_id=metadata.get("project_task_id"),
        agent_id=getattr(tool_api, "agent_id", ""),
    )
    if modes is not None:
        for index, schema in enumerate(schemas):
            if schema["name"] != MODEL_TASK_DELEGATE:
                continue
            schema = deepcopy(schema)
            mode = schema["parameters"]["properties"]["mode"]
            mode["enum"] = list(modes)
            mode["description"] = (
                "Security repair: sync starts code-bearing work; review verifies the "
                "candidate; accept applies the reviewed candidate; reject discards it."
            )
            schemas[index] = schema
    is_allowed = getattr(tool_api, "is_tool_allowed", None)
    if not callable(is_allowed):
        return schemas
    return [
        item for item in schemas if is_allowed(str(item.get("name", "") or "").strip())
    ]


def collect_runtime_tool_names(
    runner: "BrainRunner", *, metadata: dict[str, Any] | None = None
) -> frozenset[str]:
    return frozenset(
        name
        for item in collect_runtime_tool_schemas(runner, metadata=metadata)
        if (name := str(item.get("name", "") or "").strip())
    )


def build_prompt_tool_schemas(
    runner: "BrainRunner",
    *,
    user_input: str | None,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    raw_tools = collect_runtime_tool_schemas(runner, metadata=metadata)
    return _TOOL_SCHEMA_SERVICE.build_prompt_tool_schemas(
        query=user_input or "",
        tool_schemas=raw_tools,
    )


tool_schema_stub = _TOOL_SCHEMA_SERVICE.tool_stub
trim_text = _TOOL_SCHEMA_SERVICE.trim_text
tool_description = _TOOL_SCHEMA_SERVICE.tool_description
tool_parameters = _TOOL_SCHEMA_SERVICE.tool_parameters
