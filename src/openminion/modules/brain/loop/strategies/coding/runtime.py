"""Coding strategy runtime wiring, tool specs, and budget helpers."""

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
) -> dict[str, Any]:
    runtime_schema = runtime_schemas.get(tool_id, {})
    parameters = runtime_schema.get("parameters") if runtime_schema else None
    if isinstance(parameters, dict) and parameters:
        return dict(parameters)
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


def _build_tool_specs(
    allowed_tools: frozenset[str],
    *,
    ctx: ExecutionContext | None = None,
) -> list[ToolSpec]:
    descriptions: dict[str, str] = {
        "file.list_dir": "List files and directories at a path.",
        "file.read": "Read file contents.",
        "file.read_range": "Read an inclusive line-numbered range from a file.",
        "file.find": "Search for files matching a pattern.",
        "file.write": (
            "Write or overwrite a file and create parent directories "
            "automatically; use this to scaffold new project files and folders."
        ),
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
    }
    runtime_schemas = _runtime_tool_schemas_by_name(ctx)
    specs = []
    for tool_id in sorted(allowed_tools):
        specs.append(
            ToolSpec(
                name=tool_id,
                description=descriptions.get(tool_id, tool_id),
                input_schema=_input_schema_for_tool(tool_id, runtime_schemas),
            )
        )
    return specs


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
