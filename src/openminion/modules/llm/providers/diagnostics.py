from dataclasses import dataclass
import re
from typing import Any
from collections.abc import Mapping
from urllib.parse import urlparse

from ..errors import LLMCtlError


class ProviderLaneAccessState:
    ACCESS_READY = "access_ready"
    ACCESS_BLOCKED = "access_blocked"
    QUOTA_BLOCKED = "quota_blocked"
    TRANSPORT_BLOCKED = "transport_blocked"


@dataclass(frozen=True)
class ProviderLaneDescriptor:
    provider_name: str
    endpoint_lane: str
    model_name: str


@dataclass(frozen=True)
class ProviderLaneAccessClassification:
    descriptor: ProviderLaneDescriptor
    access_state: str
    reason_code: str
    error_code: str = ""
    detail_excerpt: str = ""


def classify_provider_error_category(
    *,
    error: Exception | str | None = None,
    response_text: str | None = None,
) -> str:
    classification = classify_provider_lane_access(
        provider_name="",
        model_name="",
        base_url="",
        error=error,
        response_text=response_text,
    )
    error_code = str(classification.error_code or "").strip().upper()
    if error_code in {"AUTH_ERROR", "RATE_LIMITED", "TIMEOUT", "INVALID_ARGUMENT"}:
        return error_code
    reason_code = str(classification.reason_code or "").strip().lower()
    if reason_code == "quota_or_rate_limit":
        return "RATE_LIMITED"
    if reason_code in {
        "auth_error",
        "entitlement_or_permission",
        "entitlement_or_model_access",
    }:
        return "AUTH_ERROR"
    if reason_code == "transport_error":
        return "TIMEOUT"
    if error_code == "PROVIDER_ERROR":
        return "PROVIDER_ERROR"
    return ""


def provider_lane_descriptor(
    *,
    provider_name: str,
    model_name: str,
    base_url: str,
) -> ProviderLaneDescriptor:
    return ProviderLaneDescriptor(
        provider_name=str(provider_name or "").strip().lower(),
        endpoint_lane=provider_endpoint_lane(base_url),
        model_name=str(model_name or "").strip(),
    )


def provider_lane_descriptor_from_config(
    *,
    provider_name: str,
    provider_config: Mapping[str, Any] | None,
) -> ProviderLaneDescriptor:
    payload = dict(provider_config or {})
    return provider_lane_descriptor(
        provider_name=provider_name,
        model_name=str(payload.get("model") or "").strip(),
        base_url=str(payload.get("base_url") or "").strip(),
    )


def provider_endpoint_lane(base_url: str) -> str:
    normalized = str(base_url or "").strip()
    if not normalized:
        return "default"
    parsed = urlparse(normalized)
    host = str(parsed.netloc or parsed.path or "").strip().lower()
    path = str(parsed.path or "").strip().lower()
    if "coding-intl.dashscope.aliyuncs.com" in host:
        return "dashscope_coding_intl"
    if "dashscope.aliyuncs.com" in host and "/compatible-mode/" in path:
        return "dashscope_compatible_mode"
    if "openrouter.ai" in host:
        return "openrouter_default"
    if "api.openai.com" in host:
        return "openai_default"

    host_token = host.replace(".", "_").replace("-", "_").strip("_")
    path_tokens = [
        part.replace("-", "_") for part in path.split("/") if str(part).strip()
    ]
    if path_tokens:
        return "_".join([host_token, *path_tokens]).strip("_")
    return host_token or "default"


def classify_provider_lane_access(
    *,
    provider_name: str,
    model_name: str,
    base_url: str,
    error: Exception | str | None = None,
    response_text: str | None = None,
) -> ProviderLaneAccessClassification:
    descriptor = provider_lane_descriptor(
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
    )
    body_text = str(response_text or "").strip()
    if error is None and not _looks_like_embedded_runtime_error(body_text):
        return ProviderLaneAccessClassification(
            descriptor=descriptor,
            access_state=ProviderLaneAccessState.ACCESS_READY,
            reason_code="probe_passed",
        )

    llm_error = error if isinstance(error, LLMCtlError) else None
    message = str(getattr(llm_error, "message", "") or error or body_text).strip()
    error_code = str(
        getattr(llm_error, "code", "") or ""
    ).strip() or _embedded_error_code(message)
    lowered = message.lower()

    if error_code == "AUTH_ERROR":
        return ProviderLaneAccessClassification(
            descriptor=descriptor,
            access_state=ProviderLaneAccessState.ACCESS_BLOCKED,
            reason_code=_auth_reason_code(lowered),
            error_code=error_code,
            detail_excerpt=message,
        )
    if error_code == "RATE_LIMITED" or _is_quota_block(lowered):
        return ProviderLaneAccessClassification(
            descriptor=descriptor,
            access_state=ProviderLaneAccessState.QUOTA_BLOCKED,
            reason_code="quota_or_rate_limit",
            error_code=error_code or "RATE_LIMITED",
            detail_excerpt=message,
        )
    if error_code == "TIMEOUT" or _is_transport_block(lowered):
        return ProviderLaneAccessClassification(
            descriptor=descriptor,
            access_state=ProviderLaneAccessState.TRANSPORT_BLOCKED,
            reason_code="transport_error",
            error_code=error_code or "PROVIDER_ERROR",
            detail_excerpt=message,
        )
    if _is_access_entitlement_block(lowered):
        return ProviderLaneAccessClassification(
            descriptor=descriptor,
            access_state=ProviderLaneAccessState.ACCESS_BLOCKED,
            reason_code="entitlement_or_model_access",
            error_code=error_code or "PROVIDER_ERROR",
            detail_excerpt=message,
        )
    if _is_response_envelope_issue(lowered):
        return ProviderLaneAccessClassification(
            descriptor=descriptor,
            access_state=ProviderLaneAccessState.ACCESS_READY,
            reason_code="response_envelope_error",
            error_code=error_code or "PROVIDER_ERROR",
            detail_excerpt=message,
        )
    return ProviderLaneAccessClassification(
        descriptor=descriptor,
        access_state=ProviderLaneAccessState.ACCESS_READY,
        reason_code="runtime_or_unknown_error",
        error_code=error_code or "PROVIDER_ERROR",
        detail_excerpt=message,
    )


def _auth_reason_code(message: str) -> str:
    if any(
        token in message
        for token in (
            "not entitled",
            "not enabled",
            "permission",
            "invalid api key",
        )
    ):
        return "entitlement_or_permission"
    return "auth_error"


def _is_quota_block(message: str) -> bool:
    return any(
        token in message
        for token in (
            "http 402",
            '"code":402',
            "requires more credits",
            "insufficient credits",
            "rate limited",
            "quota",
            "credit",
        )
    )


def _is_transport_block(message: str) -> bool:
    return any(
        token in message
        for token in (
            "can't assign requested address",
            "temporary failure in name resolution",
            "name or service not known",
            "nodename nor servname provided",
            "connection refused",
            "connection reset",
            "timed out",
            "timeout",
            "network is unreachable",
            "no route to host",
        )
    )


def _is_access_entitlement_block(message: str) -> bool:
    if not any(
        token in message
        for token in (
            "http 400",
            "http 401",
            "http 403",
            "http 404",
            "not entitled",
            "permission denied",
            "invalid api key",
            "api key",
            "model not found",
            "unsupported model",
            "does not exist",
            "access denied",
            "unauthorized",
            "forbidden",
        )
    ):
        return False
    return any(
        token in message
        for token in (
            "model",
            "entitled",
            "permission",
            "access",
            "forbidden",
            "unauthorized",
            "not found",
            "does not exist",
        )
    )


def _is_response_envelope_issue(message: str) -> bool:
    return any(
        token in message
        for token in (
            "response missing choices",
            "invalid choice payload",
            "missing message payload",
            "response was not valid json",
            "response was not an object",
            "malformed payload",
            "empty payload",
        )
    )


def _embedded_error_code(message: str) -> str:
    if not message:
        return ""
    match = re.search(r"(?:state machine error:\s*)?([A-Z_]+):", message)
    if match is None:
        return ""
    return str(match.group(1) or "").strip().upper()


def _looks_like_embedded_runtime_error(message: str) -> bool:
    lowered = message.lower()
    if "state machine error:" in lowered:
        return True
    return bool(_embedded_error_code(message))


__all__ = [
    "ProviderLaneAccessClassification",
    "ProviderLaneAccessState",
    "ProviderLaneDescriptor",
    "classify_provider_lane_access",
    "provider_endpoint_lane",
    "provider_lane_descriptor",
    "provider_lane_descriptor_from_config",
]
