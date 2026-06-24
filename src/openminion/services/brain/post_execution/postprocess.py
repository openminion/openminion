import hashlib
import json
import re
import time
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.types import AgentResponse, Message
from openminion.services.agent.execution.finalization import (
    extract_finalization_status_from_text,
    unwrap_final_answer_envelope,
)
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.llm.providers.envelope_v2 import CONTRACT_VERSION_V2
from openminion.services.agent.memory import MEMORY_POLICY_SNAPSHOT_VERSION
from openminion.base.constants import STATE_KEY_WORKING

_SEARCH_SOURCE_MARKER_RE = re.compile(
    r"(source=|via\s+[a-z0-9_.-]+)",
    re.IGNORECASE,
)
_SEARCH_SOURCE_LITERAL_RE = re.compile(
    r"(?:^|\n)\s*source\s*=\s*([a-z0-9_.-]+)\s*(?:$|\n)",
    re.IGNORECASE,
)
_NON_PROVIDER_SOURCE_VALUES = {"native", "fallback", "hybrid", "runtime", "model"}


def _resolve_command(*, step_out: Any) -> dict[str, Any] | None:
    action_result = getattr(step_out, "action_result", None)
    if action_result is None:
        return None
    command_id = str(getattr(action_result, "command_id", "")).strip()
    if not command_id:
        return None
    working_state = getattr(step_out, STATE_KEY_WORKING, None)
    plan = getattr(working_state, "plan", None)
    steps = getattr(plan, "steps", None)
    if not isinstance(steps, list):
        return None
    for step in steps:
        step_id = str(getattr(step, "command_id", "")).strip()
        if step_id == command_id and hasattr(step, "model_dump"):
            return step.model_dump(mode="json")
    return None


def _build_clarify_request_payload(
    *,
    step_out: Any,
    session_id: str,
    trace_id: str | None,
) -> dict[str, Any] | None:
    if str(getattr(step_out, "status", "")).strip().lower() != "waiting_user":
        return None
    working_state = getattr(step_out, STATE_KEY_WORKING, None)
    unresolved = getattr(working_state, "unresolved_clarify_items", [])
    questions: list[dict[str, Any]] = []
    if isinstance(unresolved, list):
        for raw in unresolved:
            if hasattr(raw, "model_dump"):
                item = raw.model_dump(mode="json")
            elif isinstance(raw, dict):
                item = dict(raw)
            else:
                continue
            q_text = str(item.get("question", "")).strip()
            if not q_text:
                continue
            q_id = (
                str(item.get("id", "")).strip()
                or hashlib.sha1(q_text.encode("utf-8")).hexdigest()[:12]
            )
            options = item.get("options")
            questions.append(
                {
                    "id": q_id,
                    "type": str(
                        item.get("type", "ambiguous_input") or "ambiguous_input"
                    ),
                    "question": q_text,
                    "reason_code": str(item.get("reason_code", "") or ""),
                    "source": str(item.get("source", "") or ""),
                    "options": options if isinstance(options, list) else None,
                    "default_value": item.get("default_value"),
                    "is_blocking": bool(item.get("is_blocking", True)),
                }
            )
    if not questions:
        return None
    trace_value = str(trace_id or getattr(working_state, "trace_id", "") or "").strip()
    clar_seed = f"{session_id}:{trace_value}:{','.join(q['id'] for q in questions)}"
    clarify_id = hashlib.sha1(clar_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "clarify_id": clarify_id,
        "trace_id": trace_value,
        "session_id": session_id,
        "blocking": True,
        "questions": questions,
        "defaults_used": {},
    }


def _extract_memory_policy_metadata(*, response_text: str) -> dict[str, str] | None:
    text = str(response_text or "").strip()
    if not text:
        return None

    if text.lower().startswith("memory policy snapshot:"):
        source = "runtime.config"
        version = MEMORY_POLICY_SNAPSHOT_VERSION
        for raw_line in text.splitlines():
            line = str(raw_line or "").strip()
            if line.startswith("-"):
                line = line[1:].strip()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            normalized = key.strip().lower().replace(" ", "_")
            parsed_value = value.strip()
            if normalized == "source" and parsed_value:
                source = parsed_value
            elif normalized == "version" and parsed_value:
                version = parsed_value
        return {
            "memory_policy_route": "runtime_policy_snapshot",
            "memory_policy_source": source,
            "memory_policy_version": version,
            "reason_code": "memory_policy_snapshot",
            "response_posture": "deterministic",
        }

    if text.startswith("MEMORY_POLICY: policy_unavailable"):
        source_match = re.search(r"source=([^\s)]+)", text)
        version_match = re.search(r"version=([^\s)]+)", text)
        reason_match = re.search(r"reason=([^\s)]+)", text)
        metadata = {
            "memory_policy_route": "runtime_policy_snapshot",
            "memory_policy_source": source_match.group(1)
            if source_match
            else "runtime.config",
            "memory_policy_version": version_match.group(1)
            if version_match
            else MEMORY_POLICY_SNAPSHOT_VERSION,
            "reason_code": "policy_unavailable",
            "response_posture": "degraded",
        }
        if reason_match:
            metadata["memory_policy_error"] = reason_match.group(1)
        return metadata

    return None


def _active_mode_name_from_step(step_out: Any) -> str | None:
    return (
        str(
            getattr(getattr(step_out, STATE_KEY_WORKING, None), "active_mode_name", "")
            or ""
        )
        .strip()
        .lower()
        or None
    )


def _tool_result_response_text(
    *,
    response_text: str,
    tool_results_payload: list[dict[str, Any]],
) -> str:
    current = str(response_text or "").strip()
    if not tool_results_payload:
        return current
    generic_responses = {
        "",
        "completed.",
        "completed",
        "done.",
        "done",
        "success.",
        "success",
    }
    if current.lower() not in generic_responses:
        return _append_search_source_attribution_if_needed(
            response_text=current,
            tool_results_payload=tool_results_payload,
        )
    for item in tool_results_payload:
        content = str(item.get("content", "") or "").strip()
        if content:
            return _append_search_source_attribution_if_needed(
                response_text=content,
                tool_results_payload=tool_results_payload,
            )
    return _append_search_source_attribution_if_needed(
        response_text=current,
        tool_results_payload=tool_results_payload,
    )


def _append_search_source_attribution_if_needed(
    *,
    response_text: str,
    tool_results_payload: list[dict[str, Any]],
) -> str:
    current = str(response_text or "").strip()
    if not current or not tool_results_payload:
        return current
    if _SEARCH_SOURCE_MARKER_RE.search(current):
        return current
    search_sources = _search_sources_from_tool_results(
        tool_results_payload=tool_results_payload
    )
    if not search_sources:
        return current
    return f"{current}\n\nsource={','.join(search_sources)}"


def _search_sources_from_tool_results(
    *,
    tool_results_payload: list[dict[str, Any]],
) -> list[str]:
    providers: list[str] = []
    seen: set[str] = set()
    for item in tool_results_payload:
        if not isinstance(item, dict) or not bool(item.get("ok")):
            continue
        if not _is_search_tool_result(item):
            continue
        for raw_value in _search_source_candidates(item):
            normalized = _normalize_search_provider(raw_value)
            if normalized and normalized not in seen:
                seen.add(normalized)
                providers.append(normalized)
    return sorted(providers)


def _is_search_tool_result(item: dict[str, Any]) -> bool:
    tool_name = str(item.get("tool_name", "") or "").strip().lower()
    if tool_name == "web.search" or tool_name.startswith("web.search."):
        return True
    data = item.get("data")
    return (
        isinstance(data, dict)
        and "query" in data
        and ("results" in data or "result_count" in data)
    )


def _search_source_candidates(item: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    data = item.get("data")
    if isinstance(data, dict):
        candidates.append(str(data.get("source", "") or "").strip())
    candidates.append(str(item.get("source", "") or "").strip())
    if not any(_normalize_search_provider(candidate) for candidate in candidates):
        content = str(item.get("content", "") or "")
        literal_match = _SEARCH_SOURCE_LITERAL_RE.search(content)
        if literal_match:
            candidates.append(str(literal_match.group(1) or "").strip())
    return candidates


def _normalize_search_provider(raw_value: Any) -> str | None:
    token = str(raw_value or "").strip().lower()
    if not token or token in _NON_PROVIDER_SOURCE_VALUES:
        return None
    if not re.fullmatch(r"[a-z0-9_.-]+", token):
        return None
    return token


def _coerce_tool_results_payload(raw_value: Any) -> list[dict[str, Any]]:
    if isinstance(raw_value, list):
        return [item for item in raw_value if isinstance(item, dict)]
    if isinstance(raw_value, dict):
        return [raw_value]
    if isinstance(raw_value, str):
        token = raw_value.strip()
        if not token:
            return []
        try:
            parsed = json.loads(token)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


def _tool_results_from_action_outputs(*, action_result: Any) -> list[dict[str, Any]]:
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return []
    for key in ("tool_results", "adaptive.tool_results"):
        results = _coerce_tool_results_payload(outputs.get(key))
        if results:
            return results
    return []


def _dedupe_tool_results(
    tool_results_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in tool_results_payload:
        if not isinstance(item, dict):
            continue
        stable_id = str(
            item.get("call_id", "") or item.get("id", "") or item.get("command_id", "")
        ).strip()
        if not stable_id:
            stable_id = json.dumps(item, sort_keys=True, default=str)
        if stable_id in seen:
            continue
        seen.add(stable_id)
        deduped.append(item)
    return deduped


def _cumulative_tool_results_from_step_output(
    *,
    step_out: Any,
    tool_results_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    working_state = getattr(step_out, STATE_KEY_WORKING, None)
    prior_action_result = getattr(working_state, "last_result", None)
    candidates: list[dict[str, Any]] = []
    if prior_action_result is not None:
        candidates.extend(
            _tool_results_from_action_outputs(action_result=prior_action_result)
        )
    candidates.extend(tool_results_payload or [])
    return _dedupe_tool_results(candidates)


def _action_result_termination_reason(action_result: Any | None) -> str:
    if action_result is None:
        return ""
    outputs = getattr(action_result, "outputs", None)
    if isinstance(outputs, dict):
        for key in ("adaptive.termination_reason", "termination_reason"):
            candidate = str(outputs.get(key, "") or "").strip()
            if candidate:
                return candidate
    error = getattr(action_result, "error", None)
    details = getattr(error, "details", None)
    if isinstance(details, dict):
        candidate = str(details.get("reason_code", "") or "").strip()
        if candidate:
            return candidate
    return ""


async def _apply_tool_result_postprocess(
    self,
    *,
    step_out: Any,
    message: Message,
    session_id: str,
    turn_id: str,
    active_mode_name: str | None,
    response_text: str,
    termination_reason: str,
) -> tuple[str, str, list[dict[str, Any]]]:
    action_result = getattr(step_out, "action_result", None)
    explicit_termination_reason = _action_result_termination_reason(action_result)
    if action_result is not None:
        aggregated_tool_results = _tool_results_from_action_outputs(
            action_result=action_result
        )
        if aggregated_tool_results:
            if self._telemetryctl:
                for item in aggregated_tool_results:
                    tool_name = (
                        str(item.get("tool_name", "") or "").strip() or "unknown"
                    )
                    await self._telemetryctl.emit_tool_call(
                        session_id,
                        turn_id,
                        tool_name,
                        bool(item.get("ok")),
                        active_mode_name,
                    )
            if not all(bool(item.get("ok")) for item in aggregated_tool_results):
                termination_reason = explicit_termination_reason or "tool_no_success"
            response_text = _tool_result_response_text(
                response_text=response_text,
                tool_results_payload=aggregated_tool_results,
            )
            return response_text, termination_reason, aggregated_tool_results
    command = self._resolve_command(step_out=step_out)
    if not (
        action_result is not None
        and isinstance(command, dict)
        and command.get("kind") == "tool"
    ):
        return response_text, termination_reason, []

    tool_result = self._tool_result_from_action(
        command=command,
        action_result=action_result,
    )
    tool_results_payload = [tool_result]

    if self._telemetryctl:
        tool_name = command.get("tool_name", "unknown")
        await self._telemetryctl.emit_tool_call(
            session_id,
            turn_id,
            tool_name,
            bool(tool_result.get("ok")),
            active_mode_name,
        )

    if bool(tool_result.get("ok")):
        termination_reason = "tool_final"
    else:
        termination_reason = explicit_termination_reason or "tool_no_success"
    response_text = _tool_result_response_text(
        response_text=response_text,
        tool_results_payload=tool_results_payload,
    )
    return response_text, termination_reason, tool_results_payload


def _build_turn_response_metadata(
    self,
    *,
    runner: BrainRunner,
    step_out: Any,
    session_id: str,
    request_id: str | None,
    elapsed_ms: float,
    llm_steps: int,
    termination_reason: str,
) -> dict[str, str]:
    llm_call_counts = self._collect_llm_call_counts_by_purpose(
        runner=runner,
        session_id=session_id,
        trace_id=request_id,
        fallback_total_llm_calls=max(0, int(llm_steps)),
    )
    action_outputs = getattr(getattr(step_out, "action_result", None), "outputs", None)
    if not isinstance(action_outputs, dict):
        action_outputs = {}
    total_input_tokens_used = int(action_outputs.get("total_input_tokens_used", 0) or 0)
    total_output_tokens_used = int(
        action_outputs.get("total_output_tokens_used", 0) or 0
    )
    total_tokens_used = int(action_outputs.get("total_tokens_used", 0) or 0)
    tool_calls_count = int(action_outputs.get("tool_calls_count", 0) or 0)
    _default_agent_id = resolve_default_agent_id(self._config)
    return {
        "agent": self._config.agents[_default_agent_id].name or _default_agent_id,
        "provider": str(getattr(self._provider, "name", "brain-orchestrator")),
        "model": "brain-orchestrator",
        "inference_steps": str(max(llm_steps, 1)),
        "tool_loop_max_steps": "16",
        "tool_loop_termination_reason": termination_reason,
        "elapsed_ms": str(int(elapsed_ms)),
        "turn_duration_ms": str(int(elapsed_ms)),
        "brain_status": str(getattr(step_out, "status", "completed")),
        "llm_call_counts_by_purpose": json.dumps(llm_call_counts, sort_keys=True),
        "llm_calls_count": str(sum(int(v) for v in llm_call_counts.values())),
        "tool_calls_count": str(tool_calls_count),
        "total_input_tokens_used": str(total_input_tokens_used),
        "total_output_tokens_used": str(total_output_tokens_used),
        "total_tokens_used": str(total_tokens_used),
    }


def _attach_clarify_request_metadata(
    *,
    metadata: dict[str, str],
    clarify_request: dict[str, Any] | None,
) -> None:
    if clarify_request is None:
        return
    metadata["clarify_request"] = json.dumps(
        clarify_request,
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    metadata["clarify_id"] = str(clarify_request.get("clarify_id", ""))
    metadata["clarify_question_count"] = str(len(clarify_request.get("questions", [])))


def _security_events_from_tool_results(
    *,
    tool_results_payload: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    security_events: list[dict[str, str]] = []
    metadata_updates: dict[str, str] = {}
    for item in tool_results_payload:
        data = item.get("data", {})
        if not isinstance(data, dict):
            data = {}
        error_code = str(
            item.get("error_code", "") or data.get("error_code", "")
        ).strip()
        if not error_code:
            continue
        blocked_kind = str(
            item.get("blocked_kind", "") or data.get("blocked_kind", "")
        ).strip()
        event_kind = blocked_kind or (
            "approval_required" if error_code == "require_approval" else "policy_denied"
        )
        reason_code = (
            str(item.get("reason_code", "") or data.get("reason_code", "")).strip()
            or error_code
        )
        details = {}
        raw_details = data.get("error_details")
        if isinstance(raw_details, dict):
            details = raw_details
        security_events.append(
            {
                "event_kind": event_kind,
                "reason_code": reason_code,
                "policy_version": str(details.get("policy_version", "") or "v1"),
                "decision": str(details.get("decision", "") or error_code),
                "tool_name": str(
                    item.get("tool_name", "") or item.get("name", "") or ""
                ),
                "call_id": str(item.get("call_id", "") or item.get("id", "") or ""),
                "source": "policy",
            }
        )
        if error_code.startswith("tool_budget") and details:
            metadata_updates["tool_budget"] = json.dumps(details, sort_keys=True)
    return security_events, metadata_updates


def _attach_tool_result_metadata(
    self,
    *,
    metadata: dict[str, str],
    tool_results_payload: list[dict[str, Any]],
    termination_reason: str,
) -> None:
    if not tool_results_payload:
        return
    metadata["tool_contract_version"] = CONTRACT_VERSION_V2
    ok_all = all(bool(item.get("ok")) for item in tool_results_payload)
    metadata["tool_calls_count"] = str(len(tool_results_payload))
    metadata["tool_execution_count"] = str(len(tool_results_payload))
    metadata["tool_verified"] = str(
        all(bool(item.get("verified")) for item in tool_results_payload)
    ).lower()
    metadata["tool_results"] = json.dumps(
        tool_results_payload,
        sort_keys=True,
        default=str,
    )
    if not ok_all:
        metadata["tool_loop_termination_reason"] = (
            termination_reason
            if termination_reason
            and termination_reason not in {"tool_final", "model_final"}
            else "tool_no_success"
        )
    else:
        metadata["tool_loop_termination_reason"] = (
            "tool_final" if termination_reason == "tool_final" else "model_final"
        )
    security_events, metadata_updates = _security_events_from_tool_results(
        tool_results_payload=tool_results_payload
    )
    metadata.update(metadata_updates)
    if security_events:
        metadata["security_events"] = json.dumps(security_events, sort_keys=True)


def _attach_cumulative_tool_result_metadata(
    *,
    metadata: dict[str, str],
    tool_results_payload: list[dict[str, Any]],
) -> None:
    if not tool_results_payload:
        return
    metadata["tool_calls_count_cumulative"] = str(len(tool_results_payload))
    metadata["tool_execution_count_cumulative"] = str(len(tool_results_payload))
    metadata["tool_calls_cumulative"] = json.dumps(
        tool_results_payload,
        sort_keys=True,
        default=str,
    )


def _attach_watch_outcome_metadata(
    *,
    metadata: dict[str, str],
    action_result: Any | None,
) -> None:
    if action_result is None:
        return
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return
    if "watch.condition_met" not in outputs and "watch.summary" not in outputs:
        return
    condition_met = bool(outputs.get("watch.condition_met", False))
    summary = str(outputs.get("watch.summary", "") or "").strip()
    metadata["watch_condition_met"] = str(condition_met).lower()
    metadata["watch_outcome"] = json.dumps(
        {"condition_met": condition_met, "summary": summary},
        sort_keys=True,
    )
    if summary:
        metadata["watch_summary"] = summary


def _attach_delegation_result_metadata(
    *,
    metadata: dict[str, str],
    action_result: Any | None,
) -> None:
    if action_result is None:
        return
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return
    result_summary = outputs.get("delegation_result_summary")
    if not isinstance(result_summary, dict):
        return
    metadata["delegation_result_summary"] = json.dumps(
        result_summary,
        sort_keys=True,
        default=str,
    )


_STRUCTURED_ACTION_OUTPUT_METADATA_KEYS: tuple[str, ...] = (
    "adaptive.finalization_status",
    "pending_turn_context",
    "meta_rule_preference",
    "session_work_summary",
    "goal_declaration",
    "goal_revision",
    "task_plan",
    "task_plan.step_completed",
    "task_plan.step_blocked",
    "task_plan.revision",
    "task_plan.abandoned",
    "task_plan.completed",
)


def _attach_structured_action_output_metadata(
    *,
    metadata: dict[str, str],
    action_result: Any | None,
) -> None:
    if action_result is None:
        return
    outputs = getattr(action_result, "outputs", None)
    if not isinstance(outputs, dict):
        return
    for key in _STRUCTURED_ACTION_OUTPUT_METADATA_KEYS:
        if key not in outputs:
            continue
        value = outputs.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            metadata[key] = str(value).lower()
            continue
        if isinstance(value, (dict, list)):
            metadata[key] = json.dumps(
                value,
                sort_keys=True,
                default=str,
            )
            continue
        token = str(value).strip()
        if token:
            metadata[key] = token


async def _postprocess_turn(
    self,
    *,
    runner: BrainRunner,
    step_out: Any,
    message: Message,
    history: list[Message] | None,
    session_id: str,
    request_id: str | None,
    turn_id: str,
    turn_start_time: float,
) -> AgentResponse:
    del history
    active_mode_name = self._active_mode_name_from_step(step_out)
    llm_steps = int(getattr(step_out.working_state, "llm_calls_used", 0) or 0)
    is_pae_idle_tick_noop = bool(getattr(step_out, "pae_idle_tick_noop", False))
    (
        response_text,
        termination_reason,
        tool_results_payload,
    ) = await _prepare_response_text_and_tool_results(
        self,
        step_out=step_out,
        message=message,
        session_id=session_id,
        turn_id=turn_id,
        active_mode_name=active_mode_name,
    )
    response_text, finalization_payload = _normalize_final_response_text(
        response_text=response_text,
        is_pae_idle_tick_noop=is_pae_idle_tick_noop,
    )
    elapsed_ms = (time.time() - turn_start_time) * 1000
    if self._telemetryctl:
        await self._telemetryctl.emit_tick(
            session_id,
            turn_id,
            elapsed_ms,
            active_mode_name,
        )
    metadata = _build_postprocess_response_metadata(
        self,
        runner=runner,
        step_out=step_out,
        session_id=session_id,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        llm_steps=llm_steps,
        termination_reason=termination_reason,
        tool_results_payload=tool_results_payload,
        finalization_payload=finalization_payload,
        response_text=response_text,
        is_pae_idle_tick_noop=is_pae_idle_tick_noop,
    )
    return _agent_response_from_postprocess(
        self,
        message=message,
        metadata=metadata,
        response_text=response_text,
        is_pae_idle_tick_noop=is_pae_idle_tick_noop,
    )


async def _prepare_response_text_and_tool_results(
    self,
    *,
    step_out: Any,
    message: Message,
    session_id: str,
    turn_id: str,
    active_mode_name: str | None,
) -> tuple[str, str, list[dict[str, Any]]]:
    response_text = str(step_out.message or "").strip()
    termination_reason = (
        "brain_completion" if step_out.status == "stopped" else "model_final"
    )
    return await self._apply_tool_result_postprocess(
        step_out=step_out,
        message=message,
        session_id=session_id,
        turn_id=turn_id,
        active_mode_name=active_mode_name,
        response_text=response_text,
        termination_reason=termination_reason,
    )


def _normalize_final_response_text(
    *,
    response_text: str,
    is_pae_idle_tick_noop: bool,
) -> tuple[str, dict[str, Any] | None]:
    if not response_text and not is_pae_idle_tick_noop:
        response_text = "No response generated."
    extracted_finalization = extract_finalization_status_from_text(response_text)
    if extracted_finalization is not None:
        response_text, finalization_payload = extracted_finalization
    else:
        finalization_payload = None
    unwrapped_envelope = unwrap_final_answer_envelope(response_text)
    if unwrapped_envelope is None:
        return response_text, finalization_payload
    response_text, envelope_payload = unwrapped_envelope
    if finalization_payload is None:
        finalization_payload = {
            "status": envelope_payload["status"],
            "reasoning": envelope_payload["summary"],
            "remaining_work": "",
            "blocking_reason": "",
        }
    return response_text, finalization_payload


def _build_postprocess_response_metadata(
    self,
    *,
    runner: BrainRunner,
    step_out: Any,
    session_id: str,
    request_id: str | None,
    elapsed_ms: float,
    llm_steps: int,
    termination_reason: str,
    tool_results_payload: list[dict[str, Any]],
    finalization_payload: dict[str, Any] | None,
    response_text: str,
    is_pae_idle_tick_noop: bool,
) -> dict[str, str]:
    metadata = self._build_turn_response_metadata(
        runner=runner,
        step_out=step_out,
        session_id=session_id,
        request_id=request_id,
        elapsed_ms=elapsed_ms,
        llm_steps=llm_steps,
        termination_reason=termination_reason,
    )
    metadata["respond_kind"] = str(getattr(step_out, "kind", "") or "assistant")
    metadata.update(self._identity_metadata())
    memory_policy_metadata = self._extract_memory_policy_metadata(
        response_text=response_text
    )
    if memory_policy_metadata:
        metadata.update(memory_policy_metadata)
    _attach_finish_reason(
        metadata=metadata,
        step_out=step_out,
        tool_results_payload=tool_results_payload,
        termination_reason=termination_reason,
    )
    self._attach_clarify_request_metadata(
        metadata=metadata,
        clarify_request=self._build_clarify_request_payload(
            step_out=step_out,
            session_id=session_id,
            trace_id=request_id,
        ),
    )
    _attach_telemetry_flag(self, metadata=metadata)
    _attach_postprocess_action_metadata(
        self,
        metadata=metadata,
        step_out=step_out,
        tool_results_payload=tool_results_payload,
        termination_reason=termination_reason,
        finalization_payload=finalization_payload,
    )
    if is_pae_idle_tick_noop:
        metadata["pae_idle_tick_noop"] = "true"
        metadata["finish_reason"] = "pae_idle_tick_noop"
    return metadata


def _attach_finish_reason(
    *,
    metadata: dict[str, str],
    step_out: Any,
    tool_results_payload: list[dict[str, Any]],
    termination_reason: str,
) -> None:
    status_lower = str(getattr(step_out, "status", "")).strip().lower()
    if tool_results_payload and termination_reason == "tool_final":
        metadata["finish_reason"] = "tool_calls"
    elif status_lower in {"error", "failed"}:
        metadata["finish_reason"] = "error"
    else:
        metadata["finish_reason"] = "stop"


def _attach_telemetry_flag(self, *, metadata: dict[str, str]) -> None:
    if self._telemetryctl:
        metadata["telemetry_recorded"] = "true"


def _attach_postprocess_action_metadata(
    self,
    *,
    metadata: dict[str, str],
    step_out: Any,
    tool_results_payload: list[dict[str, Any]],
    termination_reason: str,
    finalization_payload: dict[str, Any] | None,
) -> None:
    self._attach_tool_result_metadata(
        metadata=metadata,
        tool_results_payload=tool_results_payload,
        termination_reason=termination_reason,
    )
    cumulative_tool_results_payload = _cumulative_tool_results_from_step_output(
        step_out=step_out,
        tool_results_payload=tool_results_payload,
    )
    _attach_cumulative_tool_result_metadata(
        metadata=metadata,
        tool_results_payload=cumulative_tool_results_payload,
    )
    _attach_structured_action_output_metadata(
        metadata=metadata,
        action_result=getattr(step_out, "action_result", None),
    )
    if (
        finalization_payload is not None
        and "adaptive.finalization_status" not in metadata
    ):
        metadata["adaptive.finalization_status"] = json.dumps(
            finalization_payload,
            sort_keys=True,
        )
    _attach_watch_outcome_metadata(
        metadata=metadata,
        action_result=getattr(step_out, "action_result", None),
    )
    _attach_delegation_result_metadata(
        metadata=metadata,
        action_result=getattr(step_out, "action_result", None),
    )


def _agent_response_from_postprocess(
    self,
    *,
    message: Message,
    metadata: dict[str, str],
    response_text: str,
    is_pae_idle_tick_noop: bool,
) -> AgentResponse:
    if is_pae_idle_tick_noop:
        response_envelope_text = ""
    else:
        default_agent_id = resolve_default_agent_id(self._config)
        agent_name = self._config.agents[default_agent_id].name or default_agent_id
        response_envelope_text = f"{agent_name}: {response_text}"
    return AgentResponse(
        text=response_envelope_text,
        channel=message.channel,
        target=message.target,
        metadata=metadata,
    )


__all__ = [
    "_active_mode_name_from_step",
    "_apply_tool_result_postprocess",
    "_attach_clarify_request_metadata",
    "_attach_tool_result_metadata",
    "_attach_delegation_result_metadata",
    "_build_clarify_request_payload",
    "_build_turn_response_metadata",
    "_extract_memory_policy_metadata",
    "_postprocess_turn",
    "_resolve_command",
    "_security_events_from_tool_results",
    "_tool_result_response_text",
]
