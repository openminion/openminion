"""Optional mission-prefix block appenders."""

from __future__ import annotations

import json

from ..constants import CONTEXT_PURPOSE_DECIDE
from ..render.sections import _render_active_plan, _render_task_digest, _render_trailer_feedback
from ..schemas import BuildPackRequest, SessionSlice, bucket_caps_for
from .runtime import _SegmentAssemblyRuntime
from .self_awareness import render_self_awareness_block


def render_budget_telemetry_block(request: BuildPackRequest) -> str:
    payload = (
        dict(request.budget_telemetry)
        if isinstance(request.budget_telemetry, dict)
        else {}
    )
    if not payload:
        return ""
    return "[BUDGET TELEMETRY]\n" + json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        ensure_ascii=True,
    )


def _append_optional_block(
    runtime: _SegmentAssemblyRuntime,
    *,
    bucket: str,
    segment_id: str,
    content: str,
    pinned: bool = True,
) -> int:
    if not content.strip():
        return 0
    runtime.segments.append(runtime.make(segment_id, bucket, content, pinned=pinned))
    return 1


def append_budget_telemetry(
    runtime: _SegmentAssemblyRuntime, request: BuildPackRequest
) -> None:
    items = _append_optional_block(
        runtime,
        bucket="budget_telemetry",
        segment_id="budget_telemetry",
        content=render_budget_telemetry_block(request),
    )
    runtime.bucket_stats["budget_telemetry"] = {"total_available": items, "dropped": 0}


def append_self_awareness(
    runtime: _SegmentAssemblyRuntime, request: BuildPackRequest
) -> None:
    self_awareness_block = render_self_awareness_block(request)
    items = 0
    if self_awareness_block.strip():
        cap = max(
            32,
            bucket_caps_for(runtime.budgets).get(
                "self_awareness",
                runtime.budgets.instructions_tokens,
            ),
        )
        items = _append_optional_block(
            runtime,
            bucket="self_awareness",
            segment_id="self_awareness",
            content=runtime.fit_section("self_awareness", self_awareness_block, cap),
        )
    runtime.bucket_stats["self_awareness"] = {"total_available": items, "dropped": 0}


def append_task_digest(
    runtime: _SegmentAssemblyRuntime, request: BuildPackRequest, session_slice: SessionSlice
) -> None:
    items = 0
    task_digest = session_slice.task_digest
    if (
        request.purpose == CONTEXT_PURPOSE_DECIDE
        and isinstance(task_digest, dict)
        and task_digest
        and runtime.budgets.task_digest_tokens > 0
    ):
        block = runtime.fit_section(
            "task_digest",
            _render_task_digest(task_digest),
            runtime.budgets.task_digest_tokens,
        )
        items = _append_optional_block(
            runtime,
            bucket="task_digest",
            segment_id="task_digest",
            content="[TASK DIGEST]\n" + block if block.strip() else "",
        )
    runtime.bucket_stats["task_digest"] = {"total_available": items, "dropped": 0}


def append_active_plan(
    runtime: _SegmentAssemblyRuntime, request: BuildPackRequest, session_slice: SessionSlice
) -> None:
    items = 0
    active_plan = session_slice.active_task_plan
    if (
        request.purpose == CONTEXT_PURPOSE_DECIDE
        and active_plan is not None
        and active_plan.status == "active"
        and runtime.budgets.active_plan_tokens > 0
    ):
        block = runtime.fit_section(
            "active_plan",
            _render_active_plan(active_plan),
            runtime.budgets.active_plan_tokens,
        )
        items = _append_optional_block(
            runtime,
            bucket="active_plan",
            segment_id="active_plan",
            content="[ACTIVE PLAN]\n" + block if block.strip() else "",
        )
    runtime.bucket_stats["active_plan"] = {"total_available": items, "dropped": 0}


def append_trailer_feedback(
    runtime: _SegmentAssemblyRuntime, request: BuildPackRequest, session_slice: SessionSlice
) -> None:
    items = 0
    pending_feedback = session_slice.pending_trailer_feedback
    if (
        request.purpose == CONTEXT_PURPOSE_DECIDE
        and isinstance(pending_feedback, dict)
        and pending_feedback
        and runtime.budgets.trailer_feedback_tokens > 0
    ):
        block = runtime.fit_section(
            "trailer_feedback",
            _render_trailer_feedback(pending_feedback),
            runtime.budgets.trailer_feedback_tokens,
        )
        items = _append_optional_block(
            runtime,
            bucket="trailer_feedback",
            segment_id="trailer_feedback",
            content="[TRAILER FEEDBACK]\n" + block if block.strip() else "",
        )
    runtime.bucket_stats["trailer_feedback"] = {"total_available": items, "dropped": 0}
