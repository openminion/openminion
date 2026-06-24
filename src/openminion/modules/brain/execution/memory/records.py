import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS

from ...constants import DECISION_RATIONALE_MAX_CHARS as _DECISION_RATIONALE_MAX_CHARS
from ...schemas import ActionResult, PostCompletionCritique, WorkingState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


def _bounded_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _latest_trace_context(runner: "BrainRunner") -> dict[str, Any] | None:
    llm_api = getattr(runner, "llm_api", None)
    getter = getattr(llm_api, "get_last_trace_context", None)
    for value in (
        getter() if callable(getter) else None,
        getattr(llm_api, "_last_trace_context", None),
    ):
        if isinstance(value, dict):
            return dict(value)
    return None


def _thinking_excerpt_from_trace_context(
    trace_context: dict[str, Any] | None,
    *,
    max_blocks: int = 2,
    max_chars: int = 400,
) -> str:
    if not isinstance(trace_context, dict):
        return ""
    structured_rel = str(
        trace_context.get("structured_trace_filename", "") or ""
    ).strip()
    home_root_raw = str(trace_context.get("home_root", "") or "").strip()
    if not structured_rel or not home_root_raw:
        return ""
    trace_path = (Path(home_root_raw) / structured_rel).resolve()
    if not trace_path.exists():
        return ""
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    response_payload = payload.get("response")
    if not isinstance(response_payload, dict):
        return ""
    raw_blocks = response_payload.get("thinking_blocks")
    if not isinstance(raw_blocks, list):
        return ""
    excerpts: list[str] = []
    for item in raw_blocks[:max_blocks]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "") or "").strip()
        if content:
            excerpts.append(content)
    if not excerpts:
        return ""
    return _bounded_text("\n\n".join(excerpts), max_chars=max_chars)


def _decision_value(value: Any) -> Any:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if isinstance(raw, str | int | float | bool):
        return raw
    return str(raw)


def _decision_execution_target_payload(decision: Any) -> dict[str, Any]:
    execution_target = getattr(decision, "execution_target", None)
    if execution_target is None:
        return {}
    payload: dict[str, Any] = {}
    kind = _decision_value(getattr(execution_target, "kind", None))
    target_agent_id = str(
        getattr(execution_target, "target_agent_id", "") or ""
    ).strip()
    target_capability = str(
        getattr(execution_target, "target_capability", "") or ""
    ).strip()
    expect_async = bool(getattr(execution_target, "expect_async", False))
    if kind:
        payload["execution_target_kind"] = kind
    if target_agent_id:
        payload["target_agent_id"] = target_agent_id
    if target_capability:
        payload["target_capability"] = target_capability
    if expect_async:
        payload["expect_async"] = True
    return payload


def _decision_finalization_status(decision: Any) -> str | None:
    finalization_status = getattr(decision, STATE_KEY_FINALIZATION_STATUS, None)
    if finalization_status is None:
        return None
    return _decision_value(getattr(finalization_status, "status", None))


def _decision_card_content(
    *,
    decision: Any,
    state: WorkingState,
) -> dict[str, Any]:
    """Build a DRM decision card from typed decision fields only."""

    content: dict[str, Any] = {
        "type": "decision",
        "scope": "session",
        "session_id": str(getattr(state, "session_id", "") or "").strip(),
        "route_chosen": str(
            getattr(decision, "route", getattr(decision, "mode", "")) or ""
        ).strip(),
        "reason_code": str(getattr(decision, "reason_code", "") or "").strip(),
        "sub_intents": [
            str(item).strip()
            for item in list(getattr(decision, "sub_intents", []) or [])
            if str(item).strip()
        ],
        "rationale": _bounded_text(
            getattr(decision, "rationale", ""),
            max_chars=_DECISION_RATIONALE_MAX_CHARS,
        ),
        "confidence": float(getattr(decision, "confidence", 0.5) or 0.5),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    turn_id = str(getattr(state, "turn_id", "") or "").strip()
    if turn_id:
        content["turn_id"] = turn_id
    turn_index = getattr(state, "turn_index", None)
    if turn_index is not None:
        try:
            content["turn_index"] = int(turn_index)
        except (TypeError, ValueError):
            pass
    act_profile = _decision_value(getattr(decision, "act_profile", None))
    if act_profile:
        content["act_profile"] = act_profile
    respond_kind = _decision_value(getattr(decision, "respond_kind", None))
    if respond_kind:
        content["respond_kind"] = respond_kind
    finalization_status = _decision_finalization_status(decision)
    if finalization_status:
        content[STATE_KEY_FINALIZATION_STATUS] = finalization_status
    content.update(_decision_execution_target_payload(decision))
    return content


def _closure_intent_ids(state: WorkingState) -> list[str]:
    seen: set[str] = set()
    intent_ids: list[str] = []
    for item in list(getattr(state, "intent_execution_states", []) or []):
        intent_id = str(getattr(item, "intent_id", "") or "").strip()
        if intent_id and intent_id not in seen:
            seen.add(intent_id)
            intent_ids.append(intent_id)
    for item in list(getattr(state, "decision_sub_intents", []) or []):
        intent_id = str(item or "").strip()
        if intent_id and intent_id not in seen:
            seen.add(intent_id)
            intent_ids.append(intent_id)
    return intent_ids


def _post_completion_critique_content(
    *,
    critique: PostCompletionCritique,
    state: WorkingState,
) -> dict[str, Any]:
    content: dict[str, Any] = {
        "type": "post_completion_critique",
        "intent_id": critique.intent_id,
        "summary": critique.summary,
        "lessons": list(critique.lessons),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "route_chosen": str(getattr(state, "active_mode_name", "") or "").strip(),
        "sub_intents": [
            str(item).strip()
            for item in list(getattr(state, "decision_sub_intents", []) or [])
            if str(item).strip()
        ],
    }
    if critique.next_time_action:
        content["next_time_action"] = critique.next_time_action
    finalization_status = str(
        getattr(getattr(state, STATE_KEY_FINALIZATION_STATUS, None), "status", "") or ""
    ).strip()
    if finalization_status:
        content[STATE_KEY_FINALIZATION_STATUS] = finalization_status
    return content


def _dedupe_text_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _success_memory_config(runner: "BrainRunner") -> Any:
    profile_cfg = getattr(getattr(runner, "profile", None), "success_memory", None)
    if profile_cfg is not None:
        return profile_cfg
    return getattr(getattr(runner, "options", None), "success_memory_config", None)


def _all_steps_succeeded(state: WorkingState) -> bool:
    intent_states = list(getattr(state, "intent_execution_states", []) or [])
    if not intent_states:
        return True
    return all(
        str(getattr(item, "status", "") or "").strip().lower() == "succeeded"
        for item in intent_states
    )


def _successful_command_ids(
    *,
    state: WorkingState,
    action_result: ActionResult | None,
) -> list[str]:
    command_ids = [
        str(item.command_id)
        for item in list(getattr(state, "step_outputs", []) or [])
        if str(getattr(item, "command_id", "") or "").strip()
    ]
    if action_result is not None:
        command_ids.append(str(getattr(action_result, "command_id", "") or "").strip())
    return _dedupe_text_values(command_ids)


def _successful_tool_names(
    *,
    state: WorkingState,
    command_ids: list[str],
) -> list[str]:
    plan = getattr(state, "plan", None)
    if plan is None:
        return []
    names: list[str] = []
    for command in getattr(plan, "steps", []) or []:
        command_id = str(getattr(command, "command_id", "") or "").strip()
        if command_id not in command_ids:
            continue
        tool_name = str(getattr(command, "tool_name", "") or "").strip()
        if tool_name:
            names.append(tool_name)
    return _dedupe_text_values(names)


def _command_signatures(
    *,
    state: WorkingState,
    command_ids: list[str],
) -> list[str]:
    plan = getattr(state, "plan", None)
    if plan is None:
        return []
    try:
        from ..loop.tools.contracts import canonical_tool_arguments
    except Exception:
        return []
    signatures: list[str] = []
    for command in getattr(plan, "steps", []) or []:
        command_id = str(getattr(command, "command_id", "") or "").strip()
        if command_id not in command_ids:
            continue
        raw_args = getattr(command, "args", None)
        if not isinstance(raw_args, dict):
            continue
        try:
            signature = canonical_tool_arguments(dict(raw_args))
        except Exception:
            continue
        normalized = str(signature or "").strip()
        if normalized:
            signatures.append(normalized)
    return _dedupe_text_values(signatures)


def _afe_config(runner: "BrainRunner") -> Any:
    profile_cfg = getattr(
        getattr(runner, "profile", None), "auto_fact_extraction", None
    )
    if profile_cfg is not None:
        return profile_cfg
    return getattr(
        getattr(runner, "options", None), "auto_fact_extraction_config", None
    )


def _afe_model(runner: "BrainRunner", *, tier: str) -> str:
    """Resolve the model for AFE extraction from profile LLM profiles."""
    profiles = getattr(getattr(runner, "profile", None), "llm_profiles", None)
    candidates: list[str]
    if tier == "reflect":
        candidates = ["reflect_model", "decide_model", "act_model"]
    else:
        candidates = [f"{tier}_model", "reflect_model", "decide_model"]
    for attr in candidates:
        value = str(getattr(profiles, attr, "") or "").strip()
        if value:
            return value
    return ""
