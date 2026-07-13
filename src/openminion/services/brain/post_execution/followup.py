import hashlib
import time

from openminion.base.time import utc_now_iso as _iso_now_utc
from typing import Any

from openminion.base.config.core import resolve_default_agent_id
from openminion.base.types import Message
from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.llm.providers.normalization import normalize_provider_response
from openminion.modules.llm.providers.tool_calling import (
    detect_raw_envelope,
    detect_raw_tool_markup,
)
from openminion.modules.tool.base import ToolExecutionResult
from openminion.services.agent import (
    _DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
    _loop_tool_feedback,
    _map_history_to_provider,
    _resolve_system_prompt,
)
from openminion.modules.tool.exposure import get_allowed_model_tool_names


def _dated_evidence_lines_from_tool_results(
    tool_results: list[dict[str, Any]],
) -> list[str]:
    """Extract typed dated-evidence facts from structured tool results."""

    lines: list[str] = []
    seen: set[str] = set()
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        tool_name = str(item.get("tool_name", "") or "").strip()
        dated_value = ""
        for key in ("published_at", "query_time", "retrieved_at", "evidence_date"):
            candidate = str(data.get(key, "") or "").strip()
            if candidate:
                dated_value = candidate
                break
        if not dated_value:
            results = data.get("results")
            if isinstance(results, list):
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    for key in ("published_at", "date", "evidence_date"):
                        candidate = str(result.get(key, "") or "").strip()
                        if candidate:
                            dated_value = candidate
                            break
                    if dated_value:
                        break
        if not dated_value:
            continue
        fact_line = (
            f"evidence_date={dated_value}"
            if not tool_name
            else f"evidence_date={dated_value} (tool={tool_name})"
        )
        if fact_line in seen:
            continue
        seen.add(fact_line)
        lines.append(fact_line)
    return lines


def _build_runtime_facts_message(
    *,
    tool_results: list[dict[str, Any]],
) -> ProviderHistoryMessage | None:
    """Compose a typed runtime-facts system message for the follow-up call."""

    lines: list[str] = [f"current_datetime={_iso_now_utc()}"]
    lines.extend(_dated_evidence_lines_from_tool_results(tool_results))
    if not lines:
        return None
    return ProviderHistoryMessage(role="system", content="\n".join(lines))


def _looks_like_embedded_tool_call(text: str | None) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return (
        normalized.startswith("[tool_call]")
        or normalized.startswith("[system: unexecutable_tool_envelope]")
        or normalized.startswith("<tool_call")
        or detect_raw_envelope(normalized)
        or detect_raw_tool_markup(normalized)
    )


def _fallback_tool_follow_up_text(*, tool_results: list[dict[str, Any]]) -> str | None:
    for item in tool_results:
        if bool(item.get("ok")):
            content = str(item.get("content", "") or "").strip()
            if content:
                return content
    for item in tool_results:
        content = str(item.get("content", "") or "").strip()
        if content:
            return content
        error = str(item.get("error", "") or "").strip()
        if error:
            return error
    return None


def _should_replace_with_tool_fallback_text(
    *,
    follow_up: ProviderResponse,
    tool_results: list[dict[str, Any]],
) -> bool:
    if not tool_results:
        return False
    if list(getattr(follow_up, "tool_calls", []) or []):
        return True
    return _looks_like_embedded_tool_call(str(getattr(follow_up, "text", "") or ""))


def _tool_result_from_action(
    *,
    command: dict[str, Any],
    action_result: Any,
) -> dict[str, Any]:
    status = str(getattr(action_result, "status", "")).strip().lower()
    ok = status == "success"
    outputs = getattr(action_result, "outputs", {})
    data = outputs if isinstance(outputs, dict) else {}
    error_obj = getattr(action_result, "error", None)
    error_message = ""
    error_code = ""
    error_details: dict[str, Any] = {}
    if not ok:
        if error_obj is not None:
            if isinstance(error_obj, dict):
                error_message = str(error_obj.get("message", "") or "")
                error_code = str(error_obj.get("code", "") or "")
                raw_details = error_obj.get("details")
                if isinstance(raw_details, dict):
                    error_details = dict(raw_details)
            else:
                error_message = str(getattr(error_obj, "message", "") or "")
                error_code = str(getattr(error_obj, "code", "") or "")
                raw_details = getattr(error_obj, "details", None)
                if isinstance(raw_details, dict):
                    error_details = dict(raw_details)
        if not error_message:
            error_message = str(data.get("error", "") or "")
        if not error_message:
            error_message = str(getattr(action_result, "summary", "") or status)

    if error_code:
        data = dict(data)
        data["error_code"] = error_code
    if error_details:
        data = dict(data)
        data["error_details"] = error_details

    return {
        "tool_name": str(command.get("tool_name", "unknown")),
        "ok": ok,
        "verified": ok,
        "content": str(getattr(action_result, "summary", "") or ""),
        "error": error_message,
        "data": data,
        "error_code": error_code,
        "call_id": str(getattr(action_result, "command_id", "") or ""),
        "source": "native",
    }


def _record_session_event(
    *,
    session_api: Any,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
    trace_id: str | None,
) -> None:
    if session_api is None or not hasattr(session_api, "append_event"):
        return
    try:
        session_api.append_event(
            session_id,
            event_type,
            payload,
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001
        return


def _build_tool_follow_up_history(
    *,
    message: Message,
    history: list[Message] | None,
    prior_assistant_text: str,
    tool_results: list[dict[str, Any]],
) -> list[ProviderHistoryMessage]:
    # surface typed `current_datetime` (always) plus any
    provider_history = _map_history_to_provider(history or [])
    runtime_facts_message = _build_runtime_facts_message(tool_results=tool_results)
    if runtime_facts_message is not None:
        provider_history.append(runtime_facts_message)
    user_body = str(message.body or "").strip()
    if user_body:
        provider_history.append(ProviderHistoryMessage(role="user", content=user_body))

    assistant_text = str(prior_assistant_text or "").strip()
    if not assistant_text:
        names = sorted(
            {
                str(item.get("tool_name", "")).strip()
                for item in tool_results
                if str(item.get("tool_name", "")).strip()
            }
        )
        assistant_text = (
            ("Tool call requested: " + ", ".join(names))
            if names
            else "Tool call requested."
        )
    provider_history.append(
        ProviderHistoryMessage(
            role="assistant",
            content=(
                "Pre-tool draft for the same request (not the final answer):\n"
                f"{assistant_text}"
            ),
        )
    )

    feedback_items = [
        ToolExecutionResult(
            tool_name=str(item.get("tool_name", "") or "unknown"),
            ok=bool(item.get("ok")),
            content=str(item.get("content", "") or ""),
            verified=bool(item.get("verified")),
            error=str(item.get("error", "") or ""),
            data=(
                dict(item.get("data", {}))
                if isinstance(item.get("data", {}), dict)
                else {}
            ),
            call_id=str(item.get("call_id", "") or ""),
            source=str(item.get("source", "") or ""),
        )
        for item in tool_results
    ]
    provider_history.append(
        ProviderHistoryMessage(
            role="user",
            content=(
                "Tool execution results:\n"
                + _loop_tool_feedback(tool_results=feedback_items, max_chars=4000)
            ),
        )
    )
    return provider_history


def _normalize_follow_up_response(self, *, raw_follow_up: Any) -> ProviderResponse:
    follow_up_model_name = ""
    if isinstance(raw_follow_up, dict):
        follow_up_model_name = str(raw_follow_up.get("model", "") or "")
    else:
        follow_up_model_name = str(getattr(raw_follow_up, "model", "") or "")
    if self._llm_runtime is not None and isinstance(raw_follow_up, ProviderResponse):
        return raw_follow_up
    return normalize_provider_response(
        raw_follow_up,
        provider_name=str(getattr(self._provider, "name", "provider")),
        model_name=follow_up_model_name,
        allowed_tool_names=sorted(
            get_allowed_model_tool_names(self._tools)
            if self._tools is not None
            else set()
        ),
    )


def _usage_payload_from_provider_response(raw_follow_up: Any) -> dict[str, Any]:
    if isinstance(raw_follow_up, dict):
        raw_usage = raw_follow_up.get("usage")
        if isinstance(raw_usage, dict):
            return dict(raw_usage)
        return {}
    raw_usage = getattr(raw_follow_up, "usage", None)
    if isinstance(raw_usage, dict):
        return dict(raw_usage)
    if hasattr(raw_usage, "model_dump"):
        dumped = raw_usage.model_dump(mode="json", exclude_none=True)
        return dict(dumped) if isinstance(dumped, dict) else {}
    if raw_usage is not None:
        payload: dict[str, Any] = {}
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_tokens",
            "cache_creation_tokens",
        ):
            value = getattr(raw_usage, key, None)
            if value is not None:
                payload[key] = value
        return payload
    return {}


def _finalize_tool_follow_up_text(
    *,
    follow_up: ProviderResponse,
    tool_results: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    follow_up_calls = list(getattr(follow_up, "tool_calls", []) or [])
    final_text = str(getattr(follow_up, "text", "") or "").strip() or None
    final_model = str(getattr(follow_up, "model", "") or "").strip() or None
    if _should_replace_with_tool_fallback_text(
        follow_up=follow_up,
        tool_results=tool_results,
    ):
        final_text = _fallback_tool_follow_up_text(tool_results=tool_results)
    elif follow_up_calls:
        final_text = None
    return final_text, final_model


async def _follow_up_after_tool(
    self,
    *,
    message: Message,
    history: list[Message] | None,
    prior_assistant_text: str,
    tool_results: list[dict[str, Any]],
    session_id: str,
    trace_id: str | None,
) -> tuple[str | None, str | None]:
    if self._provider is None:
        return None, None

    llm_call_id = hashlib.sha1(
        f"{session_id}:{time.time_ns()}".encode("utf-8")
    ).hexdigest()[:16]
    run_id = str(getattr(message, "metadata", {}).get("run_id", "") or "").strip()
    session_api = getattr(getattr(self, "_runner", None), "session_api", None)
    _record_session_event(
        session_api=session_api,
        session_id=session_id,
        event_type="llm.call.started",
        payload={
            "llm_call_id": llm_call_id,
            "purpose": "respond_followup",
            "model": str(getattr(self._provider, "name", "provider")),
            **({"run_id": run_id} if run_id else {}),
        },
        trace_id=trace_id,
    )
    provider_history = _build_tool_follow_up_history(
        message=message,
        history=history,
        prior_assistant_text=prior_assistant_text,
        tool_results=tool_results,
    )

    _default_agent_id = resolve_default_agent_id(self._config)
    _default_profile = self._config.agents[_default_agent_id]
    raw_follow_up = await self._invoke_provider_request(
        ProviderRequest(
            user_message=_DEFAULT_TOOL_LOOP_CONTINUE_PROMPT,
            system_prompt=_resolve_system_prompt(_default_profile.system_prompt),
            thinking=_default_profile.thinking,
            history=provider_history,
            tools=[],
            tool_call_strategy="off",
            metadata={
                "channel": message.channel,
                "target": message.target,
                "loop_step": "2",
                "loop_max_steps": "16",
            },
        )
    )
    follow_up = self._normalize_follow_up_response(raw_follow_up=raw_follow_up)
    follow_up_model = str(getattr(follow_up, "model", "") or "").strip()
    _record_session_event(
        session_api=session_api,
        session_id=session_id,
        event_type="llm.call.completed",
        payload={
            "llm_call_id": llm_call_id,
            "purpose": "respond_followup",
            "model": str(
                follow_up_model or getattr(self._provider, "name", "provider")
            ),
            **({"run_id": run_id} if run_id else {}),
            "usage": _usage_payload_from_provider_response(raw_follow_up),
        },
        trace_id=trace_id,
    )
    return _finalize_tool_follow_up_text(
        follow_up=follow_up,
        tool_results=tool_results,
    )


__all__ = [
    "_build_tool_follow_up_history",
    "_fallback_tool_follow_up_text",
    "_finalize_tool_follow_up_text",
    "_follow_up_after_tool",
    "_looks_like_embedded_tool_call",
    "_normalize_follow_up_response",
    "_record_session_event",
    "_should_replace_with_tool_fallback_text",
    "_tool_result_from_action",
    "_usage_payload_from_provider_response",
]
