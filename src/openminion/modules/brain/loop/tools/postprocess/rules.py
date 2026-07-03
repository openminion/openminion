"""Postprocess rules for adaptive loop heuristics and plan lookup."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from openminion.modules.llm.contracts import (
    detect_raw_envelope,
    detect_raw_tool_markup,
    detect_raw_tool_payload_json,
)
from ..contracts import (
    AdaptiveToolLoopContext,
    AdaptiveToolLoopState,
)
from ..iteration.helpers import _count_substantive_non_control_tool_results


_HTTP_URL_RE = re.compile(r"https?://[^\s<>()\"']+")
_EXECUTION_PREFACE_RE = re.compile(
    r"\b(?:i['’]?ll|i will|we['’]?ll|we will|let me|now|next|continuing|"
    r"proceeding to)\b.*\b"
    r"(?:add|execute|run|call|fetch|write|read|verify|check|inspect|review|"
    r"continue|finish|complete|understand|fix|repair|rerun|debug)\b",
    re.IGNORECASE | re.DOTALL,
)
_PROGRESS_GERUND_RE = re.compile(
    r"^(?:reading|writing|running|checking|verifying|fetching|updating|rewriting|"
    r"inspecting|looking)\b",
    re.IGNORECASE,
)
_UNFULFILLED_FILE_PLAN_RE = re.compile(
    r"\b(?:files?\s+to\s+(?:create|write)|(?:i['’]?ll|i will|we['’]?ll|we will)"
    r"\s+(?:write|create|add)\s+(?:all\s+)?files?)\b",
    re.IGNORECASE,
)
_PLAINTEXT_FILE_WRITE_TOOL_RE = re.compile(
    r"(?ims)^\s*file\.write\s*$.*^\s*path\s*:\s*\S+.*^\s*content\s*:",
)
_PLAINTEXT_EXEC_RUN_TOOL_RE = re.compile(
    r"(?im)^\s*exec\.run\s+(?:cmd|command)\s*:",
)
_PLAINTEXT_TOOL_FUNCTION_CALL_RE = re.compile(
    r"(?im)^\s*(?:file|exec|web|search|fetch|host|process|task|plan|code)"
    r"\.[A-Za-z0-9_]+\s*\(",
)
_TOOL_ARGUMENT_KEYS = (
    '"arguments"',
    '"args"',
    '"cmd"',
    '"command"',
    '"content"',
    '"parameters"',
    '"path"',
    '"query"',
)


def _looks_like_embedded_tool_payload_json(text: str) -> bool:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if "tool_name" in payload and any(
            key in payload for key in ("tool_input", "arguments", "input")
        ):
            return True
        if "tool" in payload and isinstance(payload.get("tool"), str):
            return True
        function_payload = payload.get("function")
        if (
            isinstance(function_payload, dict)
            and str(function_payload.get("name", "") or "").strip()
        ):
            return True
    return False


def _looks_like_unexecutable_tool_payload_text(text: str) -> bool:
    token = str(text or "").strip()
    if not token:
        return False
    lower_token = token.lower()
    try:
        parsed = json.loads(token)
    except json.JSONDecodeError:
        parsed = None
    tool_result_keys = {"ok", "status", "outputs", "path", "content", "summary"}
    return (
        detect_raw_envelope(token)
        or detect_raw_tool_markup(token)
        or detect_raw_tool_payload_json(token)
        or "<tool_response>" in lower_token
        or "</tool_response>" in lower_token
        or (
            isinstance(parsed, dict)
            and (
                ("ok" in parsed and bool(tool_result_keys.intersection(parsed)))
                or ("status" in parsed and bool({"outputs", "summary"} & set(parsed)))
            )
            and any(
                key in parsed
                for key in (
                    "path",
                    "content",
                    "returned_length",
                    "bytes_written",
                    "source",
                    "outputs",
                    "summary",
                )
            )
        )
        or lower_token.startswith("[system: unexecutable_tool_envelope]")
        or lower_token.startswith("<invoke")
        or "minimax:tool_call" in lower_token
        or _looks_like_embedded_tool_payload_json(token)
        or (
            any(tool_key in lower_token for tool_key in ('"tool"', '"tool_name"'))
            and any(arg_key in lower_token for arg_key in _TOOL_ARGUMENT_KEYS)
        )
        or _PLAINTEXT_FILE_WRITE_TOOL_RE.search(token) is not None
        or _PLAINTEXT_EXEC_RUN_TOOL_RE.search(token) is not None
        or _PLAINTEXT_TOOL_FUNCTION_CALL_RE.search(token) is not None
        or (
            token.startswith("```")
            and '"tool"' in lower_token
            and (
                '"path"' in lower_token
                or '"query"' in lower_token
                or '"command"' in lower_token
            )
        )
    )


def _looks_like_structured_final_answer(text: str) -> bool:
    headings = 0
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip().rstrip(":")
        if not line or len(line) > 48:
            continue
        if any(char.isalpha() for char in line) and line.upper() == line:
            headings += 1
    return headings >= 2


def _looks_like_pre_tool_draft_echo(
    state: AdaptiveToolLoopState,
    *,
    text: str,
) -> bool:
    current = str(text or "").strip()
    if not current:
        return False
    scratchpad = dict(getattr(state, "scratchpad", {}) or {})
    prior = str(scratchpad.get("last_pre_tool_draft_text", "") or "").strip()
    if not prior:
        return False
    return current == prior


def _looks_like_execution_preface_draft(text: str) -> bool:
    current = str(text or "").strip()
    if not current:
        return False
    if _UNFULFILLED_FILE_PLAN_RE.search(current):
        return True
    if len(current) > 280:
        return False
    lowered = current.lower()
    if _looks_like_structured_final_answer(current):
        return False
    if lowered.startswith(
        (
            "based on the existing tool results",
            "continuing from the confirmed tool batch results",
            "looking at the existing tool results",
        )
    ):
        return True
    if _EXECUTION_PREFACE_RE.search(lowered):
        return True
    if _PROGRESS_GERUND_RE.search(lowered):
        return True
    return current.endswith(":")


def _final_answer_references_unbacked_source_urls(
    state: AdaptiveToolLoopState,
    *,
    text: str,
) -> bool:
    cited_urls = {
        match.group(0).rstrip(".,;:)")
        for match in _HTTP_URL_RE.finditer(str(text or ""))
    }
    if not cited_urls:
        return False
    supported_urls: set[str] = set()
    scratchpad = dict(getattr(state, "scratchpad", {}) or {})
    for item in list(scratchpad.get("adaptive.tool_results", []) or []):
        if not isinstance(item, dict) or not bool(item.get("ok")):
            continue
        rendered = json.dumps(item, sort_keys=True, ensure_ascii=False)
        for match in _HTTP_URL_RE.finditer(rendered):
            supported_urls.add(match.group(0).rstrip(".,;:)"))
    if not supported_urls:
        return False
    return any(url not in supported_urls for url in cited_urls)


def _final_text_parrots_policy_denial(
    state: AdaptiveToolLoopState,
    *,
    text: str,
) -> bool:
    current = str(text or "").strip().lower()
    if not current or "denied by policy" not in current:
        return False
    scratchpad = dict(getattr(state, "scratchpad", {}) or {})
    for item in reversed(list(scratchpad.get("adaptive.tool_results", []) or [])):
        if not isinstance(item, dict) or bool(item.get("ok")):
            continue
        if str(item.get("tool_name", "") or "").strip() != "exec.run":
            continue
        item_text = str(item.get("content", "") or "").strip().lower()
        data = item.get("data")
        error = data.get("error") if isinstance(data, dict) else None
        details = error.get("details") if isinstance(error, dict) else None
        if (
            "denied by policy" in item_text
            and isinstance(details, dict)
            and bool(str(details.get("suggested_fix", "") or "").strip())
        ):
            return current == item_text
    return False


_STATUS_ONLY_PAYLOAD_KEYS = frozenset(
    {
        "active_form",
        "confidence",
        "reasoning",
        "summary",
        "status",
    }
)


def _looks_like_structured_status_payload(text: str) -> bool:
    token = str(text or "").strip()
    if not token:
        return False
    try:
        payload = json.loads(token)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    if "active_form" not in payload:
        return False
    return set(payload).issubset(_STATUS_ONLY_PAYLOAD_KEYS)


def _raw_tool_payload_retry_allowed(
    state: AdaptiveToolLoopState,
    *,
    text: str,
    max_retries: int = 5,
) -> bool:
    token = str(text or "").strip()
    if not token:
        return False
    scratchpad = dict(state.scratchpad or {})
    prior_hashes = [
        str(item)
        for item in list(scratchpad.get("raw_tool_payload_retry_hashes", []) or [])
        if str(item)
    ]
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if digest in prior_hashes or len(prior_hashes) >= max_retries:
        return False
    prior_hashes.append(digest)
    scratchpad["raw_tool_payload_retry_hashes"] = prior_hashes
    scratchpad["raw_tool_payload_retry_count"] = len(prior_hashes)
    scratchpad["unexecutable_tool_envelope_retry_used"] = True
    state.scratchpad = scratchpad
    return True


def _active_plan_exists(loop_ctx: AdaptiveToolLoopContext) -> bool:
    session_api = getattr(loop_ctx, "session_api", None)
    get_active_task_plan = getattr(session_api, "get_active_task_plan", None)
    if not callable(get_active_task_plan):
        return False
    session_id = str(getattr(getattr(loop_ctx, "state", None), "session_id", "") or "")
    if not session_id:
        return False
    try:
        active = get_active_task_plan(session_id)
    except Exception:
        return False
    return isinstance(active, dict) and bool(active)


def _is_empty_plan_lookup_diversion(
    loop_ctx: AdaptiveToolLoopContext,
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
) -> bool:
    from openminion.modules.tool.contracts.model_ids import MODEL_PLAN_LIST

    if not tool_calls:
        return False
    if any(
        str(getattr(call, "name", "") or "").strip() != MODEL_PLAN_LIST
        for call in tool_calls
    ):
        return False
    if _active_plan_exists(loop_ctx):
        return False
    return _count_substantive_non_control_tool_results(loop_state) > 0
