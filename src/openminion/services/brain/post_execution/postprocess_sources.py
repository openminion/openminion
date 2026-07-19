from __future__ import annotations

import json
import re
from typing import Any

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
