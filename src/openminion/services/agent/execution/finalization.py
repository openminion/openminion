"""Execution finalization contracts."""

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from openminion.modules.brain.schemas import FinalizationStatus
from openminion.modules.llm.providers.base import ProviderResponse
from openminion.modules.tool import (
    blast_radius_requires_verification,
    tool_result_blast_radius,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolExecutionBatch
from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.modules.prompting.finalization import (
    FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE as _FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE,
    FINALIZATION_STATUS_RETRY_GUIDANCE as _FINALIZATION_STATUS_RETRY_GUIDANCE,
)

FINAL_ANSWER_ENVELOPE_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"status", "summary", "output"}
)
FINAL_ANSWER_ENVELOPE_ALLOWED_STATUS: frozenset[str] = frozenset(
    {"final_answer", "incomplete", "blocked"}
)

_FINALIZATION_STATUS_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<finalization_status>\s*(?P<payload>\{.*\})\s*</finalization_status>\s*$"
)
_FINALIZATION_STATUS_ATTR_RE = re.compile(
    r"(?s)(?P<body>.*?)(?:\n\s*)?<finalization_status(?P<attrs>[^>]*)>?\s*</finalization_status>\s*$"
)
_STATUS_ATTR_RE = re.compile(
    r'\bstatus\s*=\s*"(?P<status>final_answer|incomplete|blocked)"'
)
_RESPOND_ENVELOPE_RE = re.compile(
    r"(?s)^<respond(?:\((?P<call_payload>\{.*\})\)>?|>(?P<tag_payload>\{.*\})</respond>)$"
)

FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE: str = _FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE
FINALIZATION_STATUS_RETRY_GUIDANCE: str = _FINALIZATION_STATUS_RETRY_GUIDANCE

def _is_mutating_result(result: Any) -> bool:
    try:
        radius = tool_result_blast_radius(result)
    except ToolRuntimeError:
        return True
    return radius is not None and blast_radius_requires_verification(radius)


def requires_typed_finalization_contract_for_results(results: Iterable[Any]) -> bool:
    results = list(results or [])
    if len(results) >= 3:
        return True
    if any(not bool(getattr(result, "ok", False)) for result in results):
        return True
    return any(_is_mutating_result(result) for result in results)


def requires_typed_finalization_contract(batch: ToolExecutionBatch | None) -> bool:
    if batch is None:
        return False
    return requires_typed_finalization_contract_for_results(batch.results or [])


def normalize_provider_response_finalization_status(
    response: ProviderResponse,
) -> ProviderResponse:
    existing = getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
    if isinstance(existing, FinalizationStatus):
        setattr(
            response, STATE_KEY_FINALIZATION_STATUS, existing.model_dump(mode="json")
        )
        return response
    if isinstance(existing, Mapping):
        structured = _coerce_finalization_status_payload(existing)
        if structured is not None:
            setattr(
                response,
                STATE_KEY_FINALIZATION_STATUS,
                structured.model_dump(mode="json"),
            )
            return response

    extracted = extract_finalization_status_from_text(
        str(getattr(response, "text", "") or "")
    )
    if extracted is None:
        return response
    body_text, payload = extracted
    response.text = body_text
    setattr(response, STATE_KEY_FINALIZATION_STATUS, payload)
    return response


def extract_finalization_status_from_text(
    raw_text: str,
) -> tuple[str, dict[str, Any]] | None:
    text = str(raw_text or "")
    match = _FINALIZATION_STATUS_RE.match(text)
    if match:
        payload = match.group("payload") or ""
        try:
            structured = _coerce_finalization_status_payload(json.loads(payload))
        except Exception:
            structured = None
        if structured is not None:
            return str(match.group("body") or "").rstrip(), structured.model_dump(
                mode="json"
            )

    attr_match = _FINALIZATION_STATUS_ATTR_RE.match(text)
    if not attr_match:
        return None
    status_match = _STATUS_ATTR_RE.search(str(attr_match.group("attrs") or ""))
    if status_match is None:
        return None
    structured = _coerce_finalization_status_payload(
        {
            "status": str(status_match.group("status") or "").strip(),
            "reasoning": "",
            "remaining_work": "",
            "blocking_reason": "",
        }
    )
    if structured is None:
        return None
    return str(attr_match.group("body") or "").rstrip(), structured.model_dump(
        mode="json"
    )


def _coerce_finalization_status_payload(
    payload: Mapping[str, Any] | dict[str, Any],
) -> FinalizationStatus | None:
    raw = dict(payload)
    try:
        return FinalizationStatus.model_validate(
            {
                "status": str(raw.get("status", "") or "").strip(),
                "reasoning": str(
                    raw.get("reasoning")
                    or raw.get("summary")
                    or raw.get("reason")
                    or ""
                ),
                "remaining_work": str(raw.get("remaining_work") or ""),
                "blocking_reason": str(raw.get("blocking_reason") or ""),
            }
        )
    except Exception:
        return None


def finalization_status_metadata(response: ProviderResponse) -> dict[str, str]:
    payload = getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
    if isinstance(payload, FinalizationStatus):
        payload = payload.model_dump(mode="json")
    if isinstance(payload, Mapping):
        return {
            STATE_KEY_FINALIZATION_STATUS: json.dumps(dict(payload), sort_keys=True),
        }
    return {}


def unwrap_final_answer_envelope(
    raw_text: str,
) -> tuple[str, dict[str, Any]] | None:
    text = str(raw_text or "").strip()
    respond_envelope = _unwrap_respond_envelope(text)
    if respond_envelope is not None:
        return respond_envelope
    if not text or text[0] != "{" or text[-1] != "}":
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    if frozenset(parsed.keys()) != FINAL_ANSWER_ENVELOPE_REQUIRED_KEYS:
        return None
    status_value = parsed.get("status")
    summary_value = parsed.get("summary")
    output_value = parsed.get("output")
    if not isinstance(status_value, str):
        return None
    if status_value not in FINAL_ANSWER_ENVELOPE_ALLOWED_STATUS:
        return None
    if not isinstance(summary_value, str):
        return None
    if not isinstance(output_value, str):
        return None
    output_text = output_value.strip()
    if not output_text:
        return None
    payload: dict[str, Any] = {
        "status": status_value,
        "summary": summary_value,
        "output": output_value,
    }
    return output_text, payload


def _unwrap_respond_envelope(text: str) -> tuple[str, dict[str, Any]] | None:
    match = _RESPOND_ENVELOPE_RE.match(text)
    if match is None:
        return None
    payload_text = str(
        match.group("call_payload") or match.group("tag_payload") or ""
    )
    try:
        parsed = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    answer = parsed.get("answer")
    if not isinstance(answer, str):
        return None
    output_text = answer.strip()
    if not output_text:
        return None
    summary = parsed.get("summary")
    payload: dict[str, Any] = {
        "status": "final_answer",
        "summary": summary if isinstance(summary, str) else "respond envelope",
        "output": answer,
    }
    return output_text, payload


def finalization_status_termination_reason(
    response: ProviderResponse,
    *,
    default: str,
) -> str:
    payload = getattr(response, STATE_KEY_FINALIZATION_STATUS, None)
    if isinstance(payload, FinalizationStatus):
        status = payload.status
    elif isinstance(payload, Mapping):
        status = str(payload.get("status", "") or "").strip()
    else:
        status = ""
    if status == "blocked":
        return "finalization_blocked"
    if status == "incomplete":
        return "finalization_incomplete"
    return default
