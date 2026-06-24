import json
from typing import Any

from openminion.modules.llm.providers.diagnostics import (
    classify_provider_error_category,
)


def _internal_failure_answer(*, detail: str = "") -> str:
    del detail
    return (
        "I hit an internal decision error before I could continue safely on this turn."
    )


def _provider_failure_payload(
    exc: Exception,
    *,
    confidence: float,
) -> dict[str, Any] | None:
    code = str(getattr(exc, "code", "") or "").strip().upper()
    message = str(getattr(exc, "message", "") or exc or "").strip()
    details = dict(getattr(exc, "details", {}) or {})
    if not code:
        code = classify_provider_error_category(
            error=exc,
            response_text=str(
                details.get("response_text") or details.get("body_text") or ""
            ),
        )
    answer = _provider_failure_answer(code=code, message=message, details=details)
    reason_code = _provider_failure_reason_code(code)
    if not answer or not reason_code:
        return None
    return {
        "route": "respond",
        "confidence": float(confidence),
        "reason_code": reason_code,
        "respond_kind": "answer",
        "sub_intents": [],
        "rationale": "",
        "answer": answer,
    }


def _provider_failure_reason_code(code: str) -> str:
    return {
        "AUTH_ERROR": "provider_auth_failed",
        "RATE_LIMITED": "provider_rate_limited",
        "TIMEOUT": "provider_timeout",
        "PROVIDER_ERROR": "provider_error",
        "INVALID_ARGUMENT": "provider_invalid_request",
    }.get(str(code or "").strip().upper(), "")


def _provider_failure_answer(
    *,
    code: str,
    message: str,
    details: dict[str, Any],
) -> str:
    detail_excerpt = _provider_failure_detail(message=message, details=details)
    normalized_code = str(code or "").strip().upper()
    if normalized_code == "RATE_LIMITED":
        return (
            "The configured model provider could not continue this turn because it "
            "reported a quota, billing, or rate-limit block"
            + (f" ({detail_excerpt})" if detail_excerpt else "")
            + ". Please retry shortly or check the provider quota/billing state and "
            "try again."
        )
    if normalized_code == "AUTH_ERROR":
        return (
            "The configured model provider rejected authentication for this turn"
            + (f" ({detail_excerpt})" if detail_excerpt else "")
            + ". Check the provider credentials or model access and try again."
        )
    if normalized_code == "TIMEOUT":
        return (
            "The configured model provider timed out before it could return a "
            "decision"
            + (f" ({detail_excerpt})" if detail_excerpt else "")
            + ". Please retry."
        )
    if normalized_code in {"PROVIDER_ERROR", "INVALID_ARGUMENT"}:
        return (
            "The configured model provider failed before it could return a decision"
            + (f" ({detail_excerpt})" if detail_excerpt else "")
            + ". Please retry or switch models."
        )
    return ""


def _provider_failure_detail(*, message: str, details: dict[str, Any]) -> str:
    for raw in (
        details.get("response_text"),
        details.get("body_text"),
        message,
    ):
        rendered = _render_provider_error_detail(raw)
        if rendered:
            return rendered
    return ""


def _render_provider_error_detail(raw: Any) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    if token.startswith("LLM call failed:"):
        token = token.split(":", 1)[1].strip()
    parsed_message = _extract_embedded_provider_message(token)
    if parsed_message:
        return parsed_message
    compact = " ".join(token.split())
    if len(compact) > 180:
        return compact[:177] + "..."
    return compact


def _extract_embedded_provider_message(raw: str) -> str:
    token = str(raw or "").strip()
    if not token:
        return ""
    json_start = token.find("{")
    candidates = [token]
    if json_start >= 0:
        candidates.insert(0, token[json_start:])
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        error_payload = payload.get("error")
        if not isinstance(error_payload, dict):
            continue
        message = str(error_payload.get("message", "") or "").strip()
        http_code = str(error_payload.get("http_code", "") or "").strip()
        if http_code and message:
            return f"HTTP {http_code}: {message}"
        if message:
            return message
    return ""
