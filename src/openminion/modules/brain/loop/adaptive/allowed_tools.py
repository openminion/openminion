from typing import Any

from openminion.modules.brain.constants import (
    MEMORY_CONSOLIDATION_MODULE_STATE_KEY,
    WATCH_MODULE_STATE_KEY,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
)
from openminion.modules.brain.tools.schema import collect_runtime_tool_names
from openminion.modules.tool.contracts.model_ids import (
    MODEL_BROWSER,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_FILE_EDIT,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_SEARCH,
    MODEL_FILE_WRITE,
    MODEL_GIT_ADD,
    MODEL_GIT_BLAME,
    MODEL_GIT_BRANCH,
    MODEL_GIT_CHECKOUT,
    MODEL_GIT_COMMIT,
    MODEL_GIT_DIFF,
    MODEL_GIT_LOG,
    MODEL_GIT_REFLOG,
    MODEL_GIT_RESET,
    MODEL_GIT_SHOW,
    MODEL_GIT_STASH,
    MODEL_GIT_STATUS,
    MODEL_HOST_METRICS,
    MODEL_HOST_INVENTORY_REPORT,
    MODEL_IP_LOCAL,
    MODEL_IP_PUBLIC,
    MODEL_LOCATION,
    MODEL_PLAN_ADD,
    MODEL_PLAN_CLEAR,
    MODEL_PLAN_COMPLETE,
    MODEL_PLAN_LIST,
    MODEL_PLAN_SET,
    MODEL_PLAN_UPDATE,
    MODEL_TASK_CANCEL,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_SHOW,
    MODEL_TIME,
    MODEL_TOOL_LIST,
    MODEL_WEATHER,
    MODEL_WEB_FETCH,
    MODEL_WEB_SEARCH,
)


def _with_decompose_tool_spec(tool_specs: list[Any]) -> list[Any]:
    names = {
        str(getattr(spec, "name", "") or "").strip()
        for spec in tool_specs
        if str(getattr(spec, "name", "") or "").strip()
    }
    if "decompose" in names:
        return tool_specs
    from ..entry import decompose_tool_spec  # noqa: PLC0415

    return [*tool_specs, decompose_tool_spec()]


def _allows_general_decompose(*, profile_name: str, decision_reason_code: str) -> bool:
    return str(profile_name or "").strip() == "general_adaptive_v1" and str(
        decision_reason_code or ""
    ).strip() not in {
        "coding_subtask",
        "research_iteration_fallback",
    }


def _with_general_decompose_allowed_tools(
    allowed_tools: frozenset[str], *, profile_name: str, decision_reason_code: str = ""
) -> frozenset[str]:
    if not _allows_general_decompose(
        profile_name=profile_name,
        decision_reason_code=decision_reason_code,
    ):
        return frozenset(allowed_tools)
    return frozenset({*allowed_tools, "decompose"})


ACT_ADAPTIVE_ALLOWED_TOOLS = frozenset(
    {
        MODEL_FILE_LIST_DIR,
        MODEL_FILE_READ,
        MODEL_FILE_FIND,
        MODEL_FILE_WRITE,
        MODEL_FILE_SEARCH,
        MODEL_FILE_EDIT,
        MODEL_EXEC_RUN,
        MODEL_EXEC_POLL,
        MODEL_EXEC_LIST,
        MODEL_EXEC_KILL,
        MODEL_WEB_SEARCH,
        MODEL_WEB_FETCH,
        MODEL_WEATHER,
        MODEL_TIME,
        MODEL_LOCATION,
        MODEL_HOST_METRICS,
        MODEL_HOST_INVENTORY_REPORT,
        MODEL_IP_PUBLIC,
        MODEL_IP_LOCAL,
        MODEL_BROWSER,
        MODEL_TOOL_LIST,
        MODEL_TASK_SCHEDULE,
        MODEL_TASK_LIST,
        MODEL_TASK_CANCEL,
        MODEL_TASK_PAUSE,
        MODEL_TASK_RESUME,
        MODEL_TASK_SHOW,
        MODEL_GIT_STATUS,
        MODEL_GIT_DIFF,
        MODEL_GIT_LOG,
        MODEL_GIT_SHOW,
        MODEL_GIT_BLAME,
        MODEL_GIT_BRANCH,
        MODEL_GIT_CHECKOUT,
        MODEL_GIT_ADD,
        MODEL_GIT_COMMIT,
        MODEL_GIT_STASH,
        MODEL_GIT_RESET,
        MODEL_GIT_REFLOG,
        MODEL_PLAN_SET,
        MODEL_PLAN_ADD,
        MODEL_PLAN_UPDATE,
        MODEL_PLAN_COMPLETE,
        MODEL_PLAN_LIST,
        MODEL_PLAN_CLEAR,
    }
)


def _with_exposed_runtime_tools(
    tool_names: frozenset[str], *, runner: Any, session_id: str
) -> frozenset[str]:
    exposed = collect_runtime_tool_names(
        runner,
        metadata={"session_id": session_id},
    )
    return frozenset({*tool_names, *exposed})


WATCH_ADAPTIVE_ALLOWED_TOOLS = frozenset(
    {
        MODEL_FILE_LIST_DIR,
        MODEL_FILE_READ,
        MODEL_FILE_FIND,
        MODEL_EXEC_RUN,
        MODEL_WEB_SEARCH,
        MODEL_WEB_FETCH,
        MODEL_TIME,
    }
)


def _watch_profile_overrides(ctx: ExecutionContext) -> dict[str, Any] | None:
    raw = ctx.state.module_state.get(WATCH_MODULE_STATE_KEY)
    if not raw or not bool(raw.get("enabled", False)):
        return None
    turn_kind = str(raw.get("turn_kind", "") or "").strip().lower() or "check"
    action_turn = turn_kind == "action"
    raw_allowed = raw.get("allowed_tools")
    allowed_tools = WATCH_ADAPTIVE_ALLOWED_TOOLS
    if action_turn:
        allowed_tools = ACT_ADAPTIVE_ALLOWED_TOOLS
    elif isinstance(raw_allowed, list | tuple | set | frozenset):
        normalized = frozenset(
            str(item or "").strip() for item in raw_allowed if str(item or "").strip()
        )
        if normalized:
            allowed_tools = normalized
    return {
        "turn_kind": turn_kind,
        "allowed_tools": allowed_tools,
        "max_iterations": max(1, min(int(raw.get("max_iterations", 3) or 3), 3)),
        "write_authorized": bool(raw.get("write_authorized", False)),
        "profile_id": str(raw.get("profile_id", "") or "").strip(),
        "target_id": str(raw.get("target_id", "") or "").strip(),
        "task_id": str(raw.get("cron_job_id", "") or "").strip(),
    }


def _memory_consolidation_profile_overrides(
    ctx: ExecutionContext,
) -> dict[str, Any] | None:
    raw = ctx.state.module_state.get(MEMORY_CONSOLIDATION_MODULE_STATE_KEY)
    if not raw or not bool(raw.get("enabled", False)):
        return None
    return {
        "allowed_tools": frozenset(),
        "max_iterations": max(1, min(int(raw.get("max_iterations", 2) or 2), 2)),
        "target_scope": str(raw.get("target_scope", "") or "").strip(),
    }
