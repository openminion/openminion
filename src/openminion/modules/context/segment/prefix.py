"""Mission-prefix segment assembly phases."""

from __future__ import annotations

from typing import Any, Callable

from ..constants import ACTIVE_STATE_MAX_CHARS
from ..mode_ranking import _MODE_ACT, _MODE_PLAN, _MODE_RESPOND, normalize_mode_name
from ..render.sections import (
    judge_context_section,
    plan_context_section,
    reflect_context_section,
    response_instructions,
    task_header,
    validate_context_section,
)
from ..schemas import BuildConstraints, BuildPackRequest, SessionSlice
from .prefix_optional import (
    append_active_plan,
    append_budget_telemetry,
    append_self_awareness,
    append_task_digest,
    append_trailer_feedback,
)
from .runtime import _SegmentAssemblyRuntime


def tool_inventory_lines(
    *,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
) -> list[str]:
    seen: set[str] = set()
    lines: list[str] = []
    for item in list(constraints.runtime_tool_schemas) + list(prompt_tool_schemas):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("tool_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        lines.append(name)
    return lines


def _tool_schemas(
    *, constraints: BuildConstraints, prompt_tool_schemas: list[dict[str, Any]]
) -> list[Any]:
    tool_schemas: list[Any] = []
    if constraints.output_schema is not None:
        tool_schemas.append({"name": "output_schema", "schema": constraints.output_schema})
    for item in prompt_tool_schemas:
        if item not in tool_schemas:
            tool_schemas.append(item)
    return tool_schemas


def _append_static_prefix(
    runtime: _SegmentAssemblyRuntime,
    *,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
    identity_text: str,
    prefix_builder: Any,
) -> None:
    identity_block = runtime.fit_section(
        "identity", identity_text, runtime.budgets.identity_tokens
    )
    static_content = prefix_builder.build(
        identity_text=identity_block,
        tool_schemas=_tool_schemas(
            constraints=constraints,
            prompt_tool_schemas=prompt_tool_schemas,
        ),
        policy_rules=[f"safety_tag:{tag}" for tag in sorted(constraints.safety_tags)],
    )
    runtime.bucket_stats["static_prefix"] = {"total_available": 1, "dropped": 0}
    runtime.segments.append(
        runtime.make("static_prefix", "static_prefix", static_content, pinned=True)
    )


def _state_lines(
    *,
    session_slice: SessionSlice,
    project_active_state_to_prompt_view: Callable[
        [dict[str, Any] | None], tuple[Any, dict[str, int]]
    ],
    logger: Any | None,
) -> list[str]:
    state_lines: list[str] = []
    prompt_view, projection_metrics = project_active_state_to_prompt_view(
        session_slice.active_state
    )
    if prompt_view:
        view_json = prompt_view.model_dump_json()
        if len(view_json) > ACTIVE_STATE_MAX_CHARS:
            view_json = view_json[:ACTIVE_STATE_MAX_CHARS]
        state_lines.append("Active state: " + view_json)
    if projection_metrics["raw_chars"] > 0 and logger is not None:
        logger.info(
            "ASPM-05: active_state_prompt_composition metrics: raw_chars=%d projected_chars=%d chars_saved=%d",
            projection_metrics["raw_chars"],
            projection_metrics["projected_chars"],
            projection_metrics["chars_saved"],
        )
    if session_slice.open_tasks:
        state_lines.extend(f"Task: {task}" for task in session_slice.open_tasks)
    return state_lines


def _mode_context_lines(
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    inventory = tool_inventory_lines(
        constraints=constraints,
        prompt_tool_schemas=prompt_tool_schemas,
    )
    mode_name = normalize_mode_name(request.mode_name)
    if mode_name == _MODE_RESPOND:
        lines.append(
            "respond mode: favor concise summaries and recent factual context. "
            "If the session already contains a recent greeting exchange, continue the "
            "conversation instead of restarting it with the same opener."
        )
    elif mode_name == _MODE_PLAN:
        lines.append("plan mode: preserve constraints, procedures, and available tools.")
        lines.extend(f"- {name}" for name in inventory[:12])
    elif mode_name == _MODE_ACT:
        lines.append("act mode: keep tactical context for the current execution loop.")
        lines.extend(f"- {name}" for name in inventory[:8])
    return lines


def _gateway_block(runtime: _SegmentAssemblyRuntime, request: BuildPackRequest) -> str:
    gateway_ctx = str(getattr(request, "gateway_system_context", "") or "").strip()
    if not gateway_ctx:
        return ""
    return runtime.fit_section(
        "gateway_context",
        gateway_ctx,
        max(8, runtime.budgets.instructions_tokens),
    )


def _mission_content(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
    session_slice: SessionSlice,
    state_lines: list[str],
    build_clarify_digest: Callable[[dict[str, Any] | None], str],
) -> str:
    clarify_digest = build_clarify_digest(session_slice.active_state)
    clarify_block = (
        runtime.fit_section(
            "clarify_digest",
            clarify_digest,
            max(8, runtime.budgets.instructions_tokens // 2),
        )
        if clarify_digest
        else ""
    )
    constraints_block = runtime.fit_section(
        "constraints",
        response_instructions(constraints),
        runtime.budgets.instructions_tokens,
    )
    mode_lines = _mode_context_lines(
        request=request,
        constraints=constraints,
        prompt_tool_schemas=prompt_tool_schemas,
    )
    gateway_block = _gateway_block(runtime, request)
    return "\n\n".join(
        filter(
            None,
            [
                "[MODE CONTEXT]\n" + "\n".join(mode_lines) if mode_lines else "",
                f"[GATEWAY MEMORY CONTEXT]\n{gateway_block}" if gateway_block.strip() else "",
                f"[CLARIFY DIGEST]\n{clarify_block}" if clarify_block.strip() else "",
                f"[TASK HEADER]\n{task_header(request, constraints)}",
                plan_context_section(request),
                judge_context_section(request),
                reflect_context_section(request),
                validate_context_section(request),
                "\n".join(state_lines) if state_lines else "",
                f"[CONSTRAINTS & POLICY]\n{constraints_block}" if constraints_block.strip() else "",
            ],
        )
    )


def _append_mission_snapshot(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
    session_slice: SessionSlice,
    state_lines: list[str],
    build_clarify_digest: Callable[[dict[str, Any] | None], str],
) -> None:
    mission_content = _mission_content(
        runtime,
        request=request,
        constraints=constraints,
        prompt_tool_schemas=prompt_tool_schemas,
        session_slice=session_slice,
        state_lines=state_lines,
        build_clarify_digest=build_clarify_digest,
    )
    if not mission_content.strip():
        raise RuntimeError("MISSION_CONTEXT_MISSING")
    runtime.bucket_stats["mission_snapshot"] = {"total_available": 1, "dropped": 0}
    runtime.segments.append(
        runtime.make("mission_snapshot", "mission_snapshot", mission_content, pinned=True)
    )


def append_prefix_and_mission_segments(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
    identity_text: str,
    session_slice: SessionSlice,
    prefix_builder: Any,
    project_active_state_to_prompt_view: Callable[
        [dict[str, Any] | None], tuple[Any, dict[str, int]]
    ],
    build_clarify_digest: Callable[[dict[str, Any] | None], str],
    logger: Any | None,
) -> None:
    _append_static_prefix(
        runtime,
        constraints=constraints,
        prompt_tool_schemas=prompt_tool_schemas,
        identity_text=identity_text,
        prefix_builder=prefix_builder,
    )
    state_lines = _state_lines(
        session_slice=session_slice,
        project_active_state_to_prompt_view=project_active_state_to_prompt_view,
        logger=logger,
    )
    _append_mission_snapshot(
        runtime,
        request=request,
        constraints=constraints,
        prompt_tool_schemas=prompt_tool_schemas,
        session_slice=session_slice,
        state_lines=state_lines,
        build_clarify_digest=build_clarify_digest,
    )
    append_budget_telemetry(runtime, request)
    append_self_awareness(runtime, request)
    append_task_digest(runtime, request, session_slice)
    append_active_plan(runtime, request, session_slice)
    append_trailer_feedback(runtime, request, session_slice)
