import json
from typing import Any, Iterable

from ..errors import ErrorCode
from .schemas import CandidateResponse

_ALLOWED_ERROR_CODES: frozenset[ErrorCode] = frozenset(
    {
        "INVALID_ARGUMENT",
        "AUTH_ERROR",
        "RATE_LIMITED",
        "TIMEOUT",
        "PROVIDER_ERROR",
        "POLICY_DENIED",
        "INTERNAL_ERROR",
    }
)


def _safe_error_code(code: str) -> ErrorCode:
    return code if code in _ALLOWED_ERROR_CODES else "INTERNAL_ERROR"


def _first_positive_int(*values: Any) -> int:
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return 1


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().split())


def _extract_json_dict(value: str) -> dict[str, Any] | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _candidate_profile_id(
    candidates: Iterable[CandidateResponse], candidate_id: str
) -> str | None:
    for item in candidates:
        if item.candidate_id == candidate_id:
            return item.profile_id
    return None
