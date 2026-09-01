from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_INTERNAL_MODE_ACT_CODING,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.tools.schema import collect_runtime_tool_schemas
from openminion.modules.brain.schemas import ActionError, ActionResult, new_uuid
from openminion.modules.llm.schemas import ToolSpec

from .loop_state import CodingLoopState

_BOUND_VERIFICATION_TOOLS = frozenset({"file.read", "file.read_range", "exec.run"})


def _runner_and_profile_from_context(
    ctx: ExecutionContext,
) -> tuple[Any | None, Any | None]:
    services = getattr(ctx, "_services", None)
    runner = getattr(services, "runner", None) if services is not None else None
    profile = getattr(runner, "profile", None) if runner is not None else None
    if profile is None:
        options = getattr(ctx, "options", None)
        profile = getattr(options, "profile", None) or getattr(
            options,
            "agent_profile",
            None,
        )
    return runner, profile


def _coding_mode_config_from_context(ctx: ExecutionContext) -> Any | None:
    _, profile = _runner_and_profile_from_context(ctx)
    mode_config = getattr(profile, "mode_config", None) if profile is not None else None
    if not isinstance(mode_config, dict):
        return None
    return (
        mode_config.get(BRAIN_INTERNAL_MODE_ACT_CODING)
        or mode_config.get(BRAIN_ACT_PROFILE_CODING)
        or mode_config.get("coding")
    )


def _build_error_result(summary: str, code: str) -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_FAILED,
        summary=summary,
        error=ActionError(code=code, message=summary),
    )


def _build_blocked_result(summary: str, code: str) -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status=BRAIN_ACTION_STATUS_BLOCKED,
        summary=summary,
        error=ActionError(code=code, message=summary, details={"reason_code": code}),
    )


def _resolve_model(ctx: ExecutionContext) -> str:
    profile = getattr(getattr(ctx, "options", None), "profile", None)
    if profile is None:
        profile = getattr(ctx.options, "agent_profile", None)
    if profile is not None:
        llm_profiles = getattr(profile, "llm_profiles", None)
        if llm_profiles is not None:
            act_model = getattr(llm_profiles, "act_model", None)
            if act_model:
                return str(act_model)
            decide_model = getattr(llm_profiles, "decide_model", None)
            if decide_model:
                return str(decide_model)
    return ""


def _runtime_tool_schemas_by_name(
    ctx: ExecutionContext | None,
) -> dict[str, dict[str, Any]]:
    if ctx is None:
        return {}
    runner, _profile = _runner_and_profile_from_context(ctx)
    if runner is None:
        return {}
    return {
        str(item.get("name", "") or "").strip(): item
        for item in collect_runtime_tool_schemas(runner)
        if str(item.get("name", "") or "").strip()
    }


def _input_schema_for_tool(
    tool_id: str,
    runtime_schemas: dict[str, dict[str, Any]],
    verification_targets: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    runtime_schema = runtime_schemas.get(tool_id, {})
    parameters = runtime_schema.get("parameters") if runtime_schema else None
    if isinstance(parameters, dict) and parameters:
        schema = dict(parameters)
    else:
        schema = {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
    if tool_id not in _BOUND_VERIFICATION_TOOLS or not verification_targets:
        return schema
    target_kinds = [
        kind for kind in ("criterion", "deliverable") if verification_targets.get(kind)
    ]
    target_ids = [
        target_id for kind in target_kinds for target_id in verification_targets[kind]
    ]
    if not target_ids:
        return schema
    properties = dict(schema.get("properties", {}) or {})
    properties.update(
        {
            "verification_target_kind": {
                "type": "string",
                "enum": target_kinds,
                "description": "Exact typed Goal target kind for this proof.",
            },
            "verification_target_id": {
                "type": "string",
                "enum": target_ids,
                "description": "Exact criterion or deliverable ID for this proof.",
            },
        }
    )
    required = list(schema.get("required", []) or [])
    required.extend(
        name
        for name in ("verification_target_kind", "verification_target_id")
        if name not in required
    )
    return {**schema, "properties": properties, "required": required}


def _build_tool_specs(
    allowed_tools: frozenset[str],
    *,
    ctx: ExecutionContext | None = None,
    verification_targets: dict[str, tuple[str, ...]] | None = None,
) -> list[ToolSpec]:
    descriptions: dict[str, str] = {
        "file.list_dir": "List files and directories at a path.",
        "file.read": "Read file contents.",
        "file.read_range": "Read an inclusive line-numbered range from a file.",
        "file.find": "Search for files matching a pattern.",
        "file.write": (
            "Write or overwrite one complete target file path and create its parent "
            "directories automatically. Call this tool with path and content; do not "
            "print JSON path/content payloads as prose."
        ),
        "file.trash": "Move one file or directory to the system trash.",
        "code.patch": "Apply a unified-diff patch to a file.",
        "code.grep": "Search workspace text with structured grep results.",
        "code.repo_index": "Return structured workspace file, symbol, and import facts.",
        "code.repo_map": "Summarize the workspace tree and key Python symbols.",
        "code.symbol_find": "Find symbol definitions and line ranges.",
        "exec.run": (
            "Run one allowlisted direct shell command for verification or "
            "existing-file workflows; do not use pipes, redirections, chaining, "
            "fallback operators, or file/directory creation when structured tools "
            "or structured file tools can do that directly. For target "
            "directories, pass path/cwd/working_directory instead of prefixing "
            "the command with cd."
        ),
        "exec.poll": "Poll the status or output of a running process.",
        "exec.list": "List currently running processes.",
        "exec.kill": "Kill a running process by ID.",
        "agent.list": "List visible agents before choosing an exact delegation target.",
        "agent.get": "Inspect one exact visible agent before delegation.",
        "task.delegate": (
            "Delegate coding work to an exact named agent. Call agent.list first "
            "unless an exact visible agent_id is already known; never invent a role "
            "alias. For code-bearing "
            "child work, inspect the returned outputs for a child artifact "
            "record, then call task.delegate again with mode='accept' or "
            "mode='reject'; parent integration is always explicit."
        ),
    }
    runtime_schemas = _runtime_tool_schemas_by_name(ctx)
    return [
        ToolSpec(
            name=tool_id,
            description=descriptions.get(tool_id, tool_id),
            input_schema=_input_schema_for_tool(
                tool_id,
                runtime_schemas,
                verification_targets,
            ),
        )
        for tool_id in sorted(allowed_tools)
    ]


def _is_budget_exhausted(ctx: ExecutionContext, loop: CodingLoopState) -> bool:
    state = ctx.state
    budgets = state.budgets_remaining
    if budgets.tool_calls <= 0 and loop.tool_calls_made:
        return True
    if budgets.tokens <= 0:
        return True
    if state.llm_calls_used >= state.llm_calls_max:
        return True
    return False
