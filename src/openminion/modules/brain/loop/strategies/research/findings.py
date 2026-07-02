from typing import Any

from openminion.base.time import utc_now_iso as _iso_now_utc
from openminion.modules.brain.loop.failures import _internal_failure_answer

_RESEARCH_PLACEHOLDER_PREFIX = "Research iteration "
_RUNTIME_BUDGET_ONLY_MESSAGES = (
    "[act] budget exhausted before a final answer.",
    "[act] reached the adaptive iteration cap without a final answer.",
    "[act] repeated identical tool calls detected without reaching a final answer.",
    "[act] repeated the same tool pattern without reaching a final answer.",
)
_TEMPORAL_FACT_DATE_KEYS = (
    "published_at",
    "query_time",
    "retrieved_at",
    "evidence_date",
)
_TEMPORAL_FACT_RESULT_KEYS = ("published_at", "date", "evidence_date")


def normalized_text(value: Any) -> str:
    return str(value or "").strip()


def _is_placeholder_text(text: str) -> bool:
    normalized = normalized_text(text)
    if not normalized:
        return True
    return normalized.startswith(_RESEARCH_PLACEHOLDER_PREFIX)


def _is_budget_only_message(text: str) -> bool:
    normalized = normalized_text(text)
    if not normalized:
        return False
    return any(
        normalized == item or normalized.startswith(f"{item} ")
        for item in _RUNTIME_BUDGET_ONLY_MESSAGES
    )


def usable_child_result_text(text: str) -> str:
    normalized = normalized_text(text)
    if not normalized:
        return ""
    if normalized == _internal_failure_answer():
        return ""
    if _is_budget_only_message(normalized):
        return ""
    return normalized


def _dedup_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in values:
        normalized = normalized_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _evidence_dates_from_tool_results(tool_results: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        dated_value = ""
        for key in _TEMPORAL_FACT_DATE_KEYS:
            candidate = normalized_text(data.get(key))
            if candidate:
                dated_value = candidate
                break
        if not dated_value:
            results = data.get("results")
            if isinstance(results, list):
                for result in results:
                    if not isinstance(result, dict):
                        continue
                    for key in _TEMPORAL_FACT_RESULT_KEYS:
                        candidate = normalized_text(result.get(key))
                        if candidate:
                            dated_value = candidate
                            break
                    if dated_value:
                        break
        if dated_value:
            values.append(dated_value)
    return _dedup_preserve_order(values)


def _action_result_tool_result_buckets(
    action_result: Any,
) -> list[list[dict[str, Any]]]:
    outputs = dict(getattr(action_result, "outputs", {}) or {})
    buckets: list[list[dict[str, Any]]] = []
    for key in ("tool_results", "adaptive.tool_results"):
        bucket = [
            item for item in list(outputs.get(key, []) or []) if isinstance(item, dict)
        ]
        if bucket:
            buckets.append(bucket)
    return buckets


def _working_state_tool_results(working_state: Any) -> list[dict[str, Any]]:
    scratchpad = dict(getattr(working_state, "scratchpad", {}) or {})
    return [
        item
        for item in list(scratchpad.get("adaptive.tool_results", []) or [])
        if isinstance(item, dict)
    ]


def evidence_dates_from_action_result(action_result: Any) -> list[str]:
    for bucket in _action_result_tool_result_buckets(action_result):
        evidence_dates = _evidence_dates_from_tool_results(bucket)
        if evidence_dates:
            return evidence_dates
    return []


def evidence_dates_from_working_state(working_state: Any) -> list[str]:
    tool_results = _working_state_tool_results(working_state)
    return _evidence_dates_from_tool_results(tool_results) if tool_results else []


def _finding_evidence_dates(findings: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        raw = finding.get("evidence_dates")
        if not isinstance(raw, list):
            continue
        for item in raw:
            values.append(str(item or ""))
    return _dedup_preserve_order(values)


def render_temporal_fact_lines(
    findings: list[dict[str, Any]],
    *,
    now_iso_fn: Any = _iso_now_utc,
) -> list[str]:
    lines = [f"current_datetime={now_iso_fn()}"]
    for evidence_date in _finding_evidence_dates(findings)[:6]:
        lines.append(f"evidence_date={evidence_date}")
    return lines


def meaningful_partial_texts(findings: list[dict[str, Any]]) -> list[str]:
    return [
        text
        for finding in findings
        if (text := normalized_text(finding.get("content")))
        and not _is_placeholder_text(text)
        and not _is_budget_only_message(text)
    ]


def build_pause_partial_answer(findings: list[dict[str, Any]]) -> str:
    meaningful = meaningful_partial_texts(findings)
    return "\n\n".join(meaningful[:2]).strip() if meaningful else ""


def _usable_tool_result_snippets(tool_results: list[dict[str, Any]]) -> str:
    snippets: list[str] = []
    for item in tool_results[:3]:
        if not bool(item.get("ok")):
            continue
        content = normalized_text(item.get("content"))
        if not content or _is_budget_only_message(content):
            continue
        snippets.append(content[:500])
    return "\n\n".join(snippets).strip()


def usable_child_action_result_text(action_result: Any) -> str:
    for bucket in _action_result_tool_result_buckets(action_result):
        text = _usable_tool_result_snippets(bucket)
        if text:
            return text
    return ""


def usable_child_working_state_text(working_state: Any) -> str:
    tool_results = _working_state_tool_results(working_state)
    return _usable_tool_result_snippets(tool_results) if tool_results else ""
