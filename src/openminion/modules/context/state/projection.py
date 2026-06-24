import json
import re
from typing import Any

from ..constants import (
    ACTIVE_STATE_CLARIFY_DIGEST_MAX_ANSWERS as _CLARIFY_DIGEST_MAX_ANSWERS,
    ACTIVE_STATE_CLARIFY_DIGEST_MAX_CHARS as _CLARIFY_DIGEST_MAX_CHARS,
    ACTIVE_STATE_CLARIFY_DIGEST_MAX_QUESTIONS as _CLARIFY_DIGEST_MAX_QUESTIONS,
    ACTIVE_STATE_MAX_INTENT_ITEMS as _ACTIVE_STATE_MAX_INTENT_ITEMS,
)
from ..schemas import (
    ActiveStatePromptView,
    IntentExecutionPromptView,
    LastResultSummary,
    PlanProgressPromptView,
)

_SENSITIVE_KEYS = frozenset(
    {
        "stdout",
        "stderr",
        "body",
        "outputs",
        "output",
        "result",
        "data",
        "content",
        "text",
    }
)

_ACTIVE_STATE_OMIT_KEYS = frozenset(
    {
        "unresolved_clarify_items",
        "clarify_responses",
        "clarify_history",
        "clarify_transcript",
        "pending_llm_clarify_context",
        "pending_turn_context",
        "session_work_summary",
        "low_progress_signal",
    }
)


def _normalize_pending_llm_clarify_context(
    active_state: dict[str, Any] | None,
) -> dict[str, str] | None:
    if not isinstance(active_state, dict):
        return None
    raw = active_state.get("pending_llm_clarify_context")
    if not isinstance(raw, dict):
        return None
    normalized: dict[str, str] = {}
    for key in (
        "original_user_input",
        "inferred_goal",
        "unresolved_question",
        "clarify_question",
    ):
        text = str(raw.get(key, "")).strip()
        if text:
            normalized[key] = text
    known_context = raw.get("known_context")
    if isinstance(known_context, dict) and known_context:
        parts = [
            f"{str(key).strip()}={str(value).strip()}"
            for key, value in known_context.items()
            if str(key).strip() and str(value).strip()
        ]
        if parts:
            normalized["known_context"] = ", ".join(parts[:3])
    return normalized or None


def _project_artifact_refs(raw_refs: Any, *, limit: int = 5) -> list[str]:
    refs: list[str] = []
    if not isinstance(raw_refs, list):
        return refs
    for item in raw_refs:
        ref = ""
        if isinstance(item, str):
            ref = str(item).strip()
        elif isinstance(item, dict):
            ref = str(item.get("ref", "")).strip()
        if not ref:
            continue
        refs.append(ref)
        if len(refs) >= limit:
            break
    return refs


def _redact_sensitive_fields(value: Any) -> Any:
    """Recursively redact sensitive fields from dicts to prevent prompt bloat."""
    if isinstance(value, dict):
        return {
            k: _redact_sensitive_fields(v)
            for k, v in value.items()
            if k.lower() not in _SENSITIVE_KEYS
        }
    if isinstance(value, list):
        return [_redact_sensitive_fields(item) for item in value]
    return value


def _normalize_declared_sub_intents(active_state: dict[str, Any]) -> list[str]:
    raw = active_state.get("decision_sub_intents")
    if isinstance(raw, list):
        normalized = [str(item).strip() for item in raw if str(item).strip()]
        if normalized:
            return normalized[:_ACTIVE_STATE_MAX_INTENT_ITEMS]
    raw_refs = active_state.get("decision_sub_intent_refs")
    if not isinstance(raw_refs, list):
        return []
    projected: list[str] = []
    for item in raw_refs:
        if isinstance(item, dict):
            text = (
                str(item.get("description", "")).strip()
                or str(item.get("id", "")).strip()
            )
        else:
            text = (
                str(getattr(item, "description", "") or "").strip()
                or str(getattr(item, "id", "") or "").strip()
            )
        if text:
            projected.append(text)
        if len(projected) >= _ACTIVE_STATE_MAX_INTENT_ITEMS:
            break
    return projected


def _project_intent_execution_states(
    active_state: dict[str, Any],
) -> list[IntentExecutionPromptView]:
    raw = active_state.get("intent_execution_states")
    if not isinstance(raw, list):
        return []
    projected: list[IntentExecutionPromptView] = []
    for item in raw[:_ACTIVE_STATE_MAX_INTENT_ITEMS]:
        if isinstance(item, dict):
            projected.append(
                IntentExecutionPromptView(
                    intent_id=str(item.get("intent_id", "")).strip(),
                    status=str(item.get("status", "")).strip(),
                    depends_on=[
                        str(value).strip()
                        for value in list(item.get("depends_on", []) or [])
                        if str(value).strip()
                    ],
                    last_step_index=item.get("last_step_index"),
                    updated_at=str(item.get("updated_at", "")).strip() or None,
                )
            )
            continue
        projected.append(
            IntentExecutionPromptView(
                intent_id=str(getattr(item, "intent_id", "") or "").strip(),
                status=str(getattr(item, "status", "") or "").strip(),
                depends_on=[
                    str(value).strip()
                    for value in list(getattr(item, "depends_on", []) or [])
                    if str(value).strip()
                ],
                last_step_index=getattr(item, "last_step_index", None),
                updated_at=str(getattr(item, "updated_at", "") or "").strip() or None,
            )
        )
    return [
        item
        for item in projected
        if item.intent_id or item.status or item.depends_on or item.updated_at
    ]


def _project_plan_progress(
    active_state: dict[str, Any],
) -> PlanProgressPromptView | None:
    plan_raw = active_state.get("plan")
    has_plan = isinstance(plan_raw, dict)
    step_count = 0
    if isinstance(plan_raw, dict) and isinstance(plan_raw.get("steps"), list):
        step_count = len(plan_raw.get("steps", []) or [])
    try:
        cursor = int(active_state.get("cursor", 0) or 0)
    except Exception:  # noqa: BLE001
        cursor = 0
    if not has_plan and cursor <= 0:
        return None
    return PlanProgressPromptView(
        has_plan=has_plan,
        step_count=step_count,
        cursor=cursor,
    )


def _project_active_state_to_prompt_view(
    active_state: dict[str, Any] | None,
) -> tuple[ActiveStatePromptView | None, dict[str, int]]:
    """ASPM-02, ASPM-03: Project full active_state to compact prompt view."""
    if not active_state:
        return None, {"raw_chars": 0, "projected_chars": 0, "chars_saved": 0}

    raw_chars = len(json.dumps(active_state))

    last_result_raw = active_state.get("last_result")
    last_result_summary: LastResultSummary | None = None

    if last_result_raw and isinstance(last_result_raw, dict):
        last_result_summary = LastResultSummary(
            command=last_result_raw.get("command"),
            tool=last_result_raw.get("tool"),
            status=last_result_raw.get("status", "unknown"),
            exit_code=last_result_raw.get("exit_code"),
            summary=last_result_raw.get("summary", "")[:200]
            if last_result_raw.get("summary")
            else "",
            artifact_refs=_project_artifact_refs(
                last_result_raw.get("artifact_refs", [])
            ),
        )

    prompt_view = ActiveStatePromptView(
        state_ref=active_state.get("state_ref"),
        task_id=active_state.get("task_id"),
        task_description=active_state.get("task_description"),
        status=active_state.get("status", "idle"),
        last_result=last_result_summary,
        open_questions=active_state.get("open_questions", [])[:10],
        declared_sub_intents=_normalize_declared_sub_intents(active_state),
        intent_execution_states=_project_intent_execution_states(active_state),
        plan_progress=_project_plan_progress(active_state),
        metadata={
            k: _redact_sensitive_fields(v) if isinstance(v, dict) else v
            for k, v in active_state.items()
            if k
            not in (
                "last_result",
                "open_questions",
                "state_ref",
                "task_id",
                "task_description",
                "status",
                "plan",
                "cursor",
                "decision_sub_intents",
                "decision_sub_intent_refs",
                "intent_execution_states",
                *_ACTIVE_STATE_OMIT_KEYS,
            )
        },
    )

    projected_chars = len(prompt_view.model_dump_json())
    chars_saved = max(0, raw_chars - projected_chars)

    return prompt_view, {
        "raw_chars": raw_chars,
        "projected_chars": projected_chars,
        "chars_saved": chars_saved,
    }


def _build_clarify_digest(active_state: dict[str, Any] | None) -> str:
    if not isinstance(active_state, dict):
        return ""

    pending_raw = active_state.get("unresolved_clarify_items")
    responses_raw = active_state.get("clarify_responses")
    defaults_raw = active_state.get("defaults_used")

    pending_questions: list[tuple[str, str]] = []
    if isinstance(pending_raw, list):
        for item in pending_raw:
            if not isinstance(item, dict):
                continue
            q_id = str(item.get("id", "")).strip()
            q_text = str(item.get("question", "")).strip()
            if not q_text:
                continue
            pending_questions.append((q_id, q_text))
    elif isinstance(pending_raw, dict):
        remaining = pending_raw.get("remaining_questions")
        if isinstance(remaining, list):
            for item in remaining:
                if not isinstance(item, dict):
                    continue
                q_id = str(item.get("id", "")).strip()
                q_text = str(item.get("question", "")).strip()
                if not q_text:
                    continue
                pending_questions.append((q_id, q_text))

    selected_answers: list[tuple[str, str]] = []
    if isinstance(responses_raw, dict):
        for key, value in responses_raw.items():
            answer = str(value).strip()
            if not answer:
                continue
            selected_answers.append((str(key).strip(), answer))

    defaults_used: dict[str, str] = {}
    if isinstance(defaults_raw, dict):
        for key, value in defaults_raw.items():
            defaults_used[str(key).strip()] = str(value).strip()

    llm_context = _normalize_pending_llm_clarify_context(active_state)

    if (
        not pending_questions
        and not selected_answers
        and not defaults_used
        and not llm_context
    ):
        return ""

    lines: list[str] = []
    if pending_questions:
        lines.append("pending_questions:")
        for q_id, question in pending_questions[:_CLARIFY_DIGEST_MAX_QUESTIONS]:
            short_q = re.sub(r"\s+", " ", question).strip()[:160]
            prefix = f"({q_id}) " if q_id else ""
            lines.append(f"- {prefix}{short_q}")
    if selected_answers:
        lines.append("selected_answers:")
        for q_id, answer in selected_answers[:_CLARIFY_DIGEST_MAX_ANSWERS]:
            short_a = re.sub(r"\s+", " ", answer).strip()[:120]
            label = q_id or "answer"
            lines.append(f"- {label}: {short_a}")
    if defaults_used:
        lines.append("defaults_used:")
        for key in sorted(defaults_used.keys())[:_CLARIFY_DIGEST_MAX_ANSWERS]:
            short_v = re.sub(r"\s+", " ", defaults_used[key]).strip()[:80]
            lines.append(f"- {key}: {short_v}")
    if llm_context:
        lines.append("pending_conversational_clarification:")
        for key in (
            "original_user_input",
            "inferred_goal",
            "known_context",
            "clarify_question",
            "unresolved_question",
        ):
            value = str(llm_context.get(key, "")).strip()
            if value:
                short_v = re.sub(r"\s+", " ", value).strip()[:160]
                lines.append(f"- {key}: {short_v}")

    digest = "\n".join(lines).strip()
    if len(digest) > _CLARIFY_DIGEST_MAX_CHARS:
        digest = digest[:_CLARIFY_DIGEST_MAX_CHARS].rstrip() + "\n...[truncated]"
    return digest
