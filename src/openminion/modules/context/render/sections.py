from __future__ import annotations

import json
from typing import Any, Callable

from ..constants import (
    ARTIFACT_PREVIEW_MAX_BULLETS,
    ARTIFACT_PREVIEW_MAX_CHARS,
)
from ..schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    FactRecord,
    MemoryCard,
    RenderMessage,
    TaskPlan,
    TokenReport,
)


def _request_hints_for_purpose(
    request: BuildPackRequest, purpose: str
) -> dict[str, Any]:
    if request.purpose != purpose:
        return {}
    hints = request.phase_hints
    return hints if isinstance(hints, dict) and hints else {}


def _append_json_context_line(
    lines: list[str], label: str, value: Any, *, sort_keys: bool = True
) -> None:
    if not value:
        return
    lines.append(
        f"{label}: " + json.dumps(value, ensure_ascii=True, sort_keys=sort_keys)
    )


def render_fact_table(
    facts: list[FactRecord],
    max_tokens: int,
    *,
    fit_to_budget: Callable[[str, int], tuple[str, bool]],
) -> str:
    lines = ["Facts:"]
    for item in facts:
        if not item.ttl_valid:
            continue
        lines.append(f"- ({item.record_id}) {item.text}")
    text = "\n".join(lines)
    fitted, _ = fit_to_budget(text, max_tokens)
    return fitted


def render_memory_cards(
    records: list[MemoryCard],
    max_tokens: int,
    *,
    fit_to_budget: Callable[[str, int], tuple[str, bool]],
) -> str:
    lines = ["Memory cards:"]
    for item in records:
        pin = " pinned" if item.pinned else ""
        lines.append(f"- ({item.record_type}{pin}) ({item.record_id}) {item.text}")
    text = "\n".join(lines)
    fitted, _ = fit_to_budget(text, max_tokens)
    return fitted


def render_artifact_digest(
    digest: ArtifactDigest,
    max_tokens: int,
    *,
    fit_to_budget: Callable[[str, int], tuple[str, bool]],
) -> str:
    lines = [f"Artifact: {digest.ref}"]
    if digest.view_id:
        lines.append(f"view: {digest.view_id}")
    if digest.digest_hash:
        lines.append(f"digest_hash: {digest.digest_hash}")
    lines.extend(f"- {item}" for item in digest.bullets[:ARTIFACT_PREVIEW_MAX_BULLETS])
    if digest.excerpt:
        lines.append(f"excerpt: {digest.excerpt[:ARTIFACT_PREVIEW_MAX_CHARS]}")
    text = "\n".join(lines)
    fitted, _ = fit_to_budget(text, max_tokens)
    return fitted


def render_procedure_snippet(
    proc: Any,
    max_tokens: int,
    *,
    fit_to_budget: Callable[[str, int], tuple[str, bool]],
) -> str:
    if proc is None:
        return ""
    lines = [f"Procedure: {proc.title} ({proc.procedure_id})"]
    if proc.preflight:
        lines.extend(f"- {item}" for item in proc.preflight)
    if proc.steps:
        lines.extend(f"{i + 1}. {item}" for i, item in enumerate(proc.steps[:10]))
    if proc.rollback_hint:
        lines.append(f"Rollback: {proc.rollback_hint}")
    text = "\n".join(lines)
    fitted, _ = fit_to_budget(text, max_tokens)
    return fitted


def task_header(request: BuildPackRequest, constraints: BuildConstraints) -> str:
    lines = [f"purpose: {request.purpose}", f"query: {request.query.strip()}"]
    if constraints.style_overrides:
        lines.append(
            "style_overrides: "
            + json.dumps(constraints.style_overrides, sort_keys=True)
        )
    if constraints.safety_tags:
        lines.append("safety_tags: " + ", ".join(constraints.safety_tags))
    if constraints.output_schema:
        lines.append(
            "output_schema: " + json.dumps(constraints.output_schema, sort_keys=True)
        )
    return "\n".join(lines)


def plan_context_section(request: BuildPackRequest) -> str:
    hints = _request_hints_for_purpose(request, "plan")
    if not hints:
        return ""

    lines: list[str] = []
    sub_intents = hints.get("plan_sub_intents")
    if isinstance(sub_intents, list):
        _append_json_context_line(lines, "declared_sub_intents", sub_intents)
    completed = hints.get("completed_intent_states")
    if isinstance(completed, list):
        _append_json_context_line(lines, "completed_intent_states", completed)
    remaining = hints.get("remaining_intent_states")
    if isinstance(remaining, list):
        _append_json_context_line(lines, "remaining_intent_states", remaining)
    blocked = hints.get("blocked_intent_states")
    if isinstance(blocked, list):
        _append_json_context_line(lines, "blocked_intent_states", blocked)
    adaptive_context = hints.get("adaptive_revision_context")
    if isinstance(adaptive_context, dict):
        _append_json_context_line(lines, "adaptive_revision_context", adaptive_context)
    if not lines:
        return ""
    return "[PLAN CONTEXT]\n" + "\n".join(lines)


def judge_context_section(request: BuildPackRequest) -> str:
    hints = _request_hints_for_purpose(request, "judge")
    if not hints:
        return ""

    lines: list[str] = []
    candidate_reason = str(hints.get("closure_candidate_reason") or "").strip()
    if candidate_reason:
        lines.append(f"candidate_reason: {candidate_reason}")

    action_summary = str(hints.get("closure_action_summary") or "").strip()
    if action_summary:
        lines.append(f"action_summary: {action_summary}")

    sub_intents = hints.get("closure_sub_intents")
    if isinstance(sub_intents, list):
        _append_json_context_line(lines, "sub_intents", sub_intents, sort_keys=False)

    intent_outcomes = hints.get("closure_intent_outcomes")
    if isinstance(intent_outcomes, list):
        _append_json_context_line(lines, "intent_outcomes", intent_outcomes)

    success_criteria = hints.get("closure_success_criteria")
    if isinstance(success_criteria, dict):
        _append_json_context_line(lines, "success_criteria", success_criteria)

    if not lines:
        return ""
    return "[JUDGE CONTEXT]\n" + "\n".join(lines)


def reflect_context_section(request: BuildPackRequest) -> str:
    hints = _request_hints_for_purpose(request, "reflect")
    if not hints:
        return ""

    lines: list[str] = []
    context_kind = str(hints.get("reflection_context_kind") or "").strip()
    if context_kind:
        lines.append(f"context_kind: {context_kind}")
    goal_summary = str(hints.get("reflection_goal_summary") or "").strip()
    if goal_summary:
        lines.append(f"goal_summary: {goal_summary}")
    step_context = hints.get("reflection_step_context")
    if isinstance(step_context, dict):
        _append_json_context_line(lines, "step_context", step_context)
    prior_outcomes = hints.get("reflection_prior_outcomes")
    if isinstance(prior_outcomes, list):
        _append_json_context_line(lines, "prior_outcomes", prior_outcomes)
    success_criteria = hints.get("reflection_success_criteria")
    if isinstance(success_criteria, dict):
        _append_json_context_line(lines, "success_criteria", success_criteria)
    if not lines:
        return ""
    return "[REFLECT CONTEXT]\n" + "\n".join(lines)


def validate_context_section(request: BuildPackRequest) -> str:
    hints = _request_hints_for_purpose(request, "validate")
    if not hints:
        return ""

    lines: list[str] = []
    sub_intents = hints.get("feasibility_sub_intents")
    if isinstance(sub_intents, list):
        _append_json_context_line(lines, "sub_intents", sub_intents)
    plan_steps = hints.get("feasibility_plan_steps")
    if isinstance(plan_steps, list):
        _append_json_context_line(lines, "plan_steps", plan_steps)
    runtime_facts = hints.get("feasibility_runtime_facts")
    if isinstance(runtime_facts, list):
        _append_json_context_line(lines, "runtime_facts", runtime_facts)
    if not lines:
        return ""
    return "[VALIDATE CONTEXT]\n" + "\n".join(lines)


def response_instructions(constraints: BuildConstraints) -> str:
    lines = [
        "Respond directly and stay within requested purpose.",
        "Do not invent unavailable facts; cite uncertainty when needed.",
    ]
    if constraints.output_schema:
        lines.append("Follow output schema strictly.")
    return "\n".join(lines)


def _render_trailer_feedback(feedback: dict[str, Any]) -> str:
    """Render PTCH trailer validator feedback as structured bullets.

    Pure rendering — runtime does not re-interpret the feedback payload;
    it transports the structured hints verbatim for the model to consume.
    """
    lines: list[str] = []
    kind = str(feedback.get("kind") or "").strip()
    route = str(feedback.get("route") or "").strip()
    if kind:
        lines.append(f"kind: {kind}")
    if route:
        lines.append(f"route: {route}")
    missing_lanes = feedback.get("missing_lanes")
    if isinstance(missing_lanes, list) and missing_lanes:
        lines.append("missing_lanes: " + json.dumps(missing_lanes, ensure_ascii=True))
    hints = feedback.get("hints")
    if isinstance(hints, list) and hints:
        lines.append("hints:")
        for hint in hints:
            hint_text = str(hint or "").strip()
            if hint_text:
                lines.append(f"- {hint_text}")
    return "\n".join(lines)


def _render_active_plan(plan: TaskPlan) -> str:
    """Render the model-authored active plan without adding semantic judgment."""
    lines = [
        f"plan_id: {plan.plan_id}",
        f"status: {plan.status}",
        f"objective: {plan.objective}",
        "steps:",
    ]
    for step in plan.steps:
        step_parts = [
            f"id={step.step_id}",
            f"status={step.status}",
            f"difficulty={step.estimated_difficulty}",
        ]
        if step.depends_on:
            step_parts.append(
                "depends_on=" + json.dumps(step.depends_on, ensure_ascii=True)
            )
        if step.tool_families:
            step_parts.append(
                "tool_families=" + json.dumps(step.tool_families, ensure_ascii=True)
            )
        lines.append(f"- {'; '.join(step_parts)}")
        lines.append(f"  description: {step.description}")
        if step.output_summary:
            lines.append(f"  output_summary: {step.output_summary}")
        if step.blocker_type:
            lines.append(f"  blocker_type: {step.blocker_type}")
        if step.blocker_details:
            lines.append(f"  blocker_details: {step.blocker_details}")
    return "\n".join(lines)


def _render_task_digest(digest: dict[str, Any]) -> str:
    """Render durable task digest fields without ranking or interpretation."""

    lines: list[str] = []
    current = digest.get("current_task")
    if isinstance(current, dict):
        lines.append("current_task:")
        lines.extend(_render_task_digest_task(current))
    for field_name in ("tasks_active", "tasks_ready"):
        values = digest.get(field_name)
        if isinstance(values, list) and values:
            lines.append(f"{field_name}:")
            for item in values[:5]:
                if isinstance(item, dict):
                    lines.extend(_render_task_digest_task(item))
    blockers = digest.get("blockers")
    if isinstance(blockers, list) and blockers:
        lines.append("blockers:")
        for blocker in blockers[:5]:
            text = str(blocker or "").strip()
            if text:
                lines.append(f"- {text}")
    return "\n".join(lines)


def _render_task_digest_task(task: dict[str, Any]) -> list[str]:
    parts = [
        f"id={str(task.get('task_id') or '').strip()}",
        f"status={str(task.get('status') or '').strip()}",
    ]
    next_step = str(task.get("next_step_id") or "").strip()
    if next_step:
        parts.append(f"next_step_id={next_step}")
    title = str(task.get("title") or "").strip()
    lines = [f"- {'; '.join(part for part in parts if part)}"]
    if title:
        lines.append(f"  title: {title}")
    next_title = str(task.get("next_step_title") or "").strip()
    if next_title:
        lines.append(f"  next_step_title: {next_title}")
    return lines


def estimate_tokens(
    messages: list[RenderMessage],
    *,
    estimate_text_tokens: Callable[[str], int],
) -> TokenReport:
    per_message = [estimate_text_tokens(item.content) for item in messages]
    return TokenReport(
        total_tokens=sum(per_message),
        per_message_tokens=per_message,
    )
