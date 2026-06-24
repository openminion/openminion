from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..schemas import LLMResponse, Message, ResponseError, ToolCall, UsageInfo


class ProviderAdapterResult(BaseModel):
    """Provider-neutral adapter output before core LLMResponse construction."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    output_text: str = ""
    assistant_messages: List[Message] = Field(default_factory=list)
    tool_calls: List[ToolCall] = Field(default_factory=list)
    thinking: List[Dict[str, Any]] = Field(default_factory=list)
    usage: UsageInfo = Field(default_factory=UsageInfo)
    latency_ms: int = 0
    cost_usd: Optional[float] = None
    finish_reason: str = ""
    provider_raw: Optional[Dict[str, Any]] = None
    error: Optional[ResponseError] = None
    telemetry: Dict[str, Any] = Field(default_factory=dict)
    normalization_meta: Dict[str, Any] = Field(default_factory=dict)


ProviderOutput = Union[LLMResponse, ProviderAdapterResult, Dict[str, Any]]


def _coerce_positive_float(value: Any) -> float | None:
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _extract_cache_telemetry_from_provider_raw(
    provider_raw: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if not isinstance(provider_raw, dict):
        return {}

    payload: Dict[str, Any] = {}
    cache_hit = provider_raw.get("cache_hit")
    if isinstance(cache_hit, bool) and cache_hit:
        payload["cache_hit"] = True

    usage = provider_raw.get("usage")
    if isinstance(usage, dict):
        cached_value = _coerce_positive_float(usage.get("cached_tokens"))
        if cached_value is not None:
            payload["cache_hit"] = True
            payload["cached_tokens"] = cached_value
    return payload


def _normalize_response_telemetry(response: LLMResponse) -> LLMResponse:
    telemetry = dict(response.telemetry or {})
    cache_telemetry = _extract_cache_telemetry_from_provider_raw(response.provider_raw)
    if not cache_telemetry:
        return response

    telemetry.setdefault("cache_hit", cache_telemetry.get("cache_hit"))
    if "cached_tokens" in cache_telemetry and "cached_tokens" not in telemetry:
        telemetry["cached_tokens"] = cache_telemetry["cached_tokens"]
    return response.model_copy(update={"telemetry": telemetry})


def adapter_result_to_llm_response(result: ProviderAdapterResult) -> LLMResponse:
    assistant_messages = list(result.assistant_messages or [])
    if result.output_text and not assistant_messages:
        assistant_messages = [Message(role="assistant", content=result.output_text)]

    usage = result.usage
    if usage.total_tokens is None:
        usage = usage.model_copy(
            update={
                "total_tokens": int(usage.input_tokens or 0)
                + int(usage.output_tokens or 0)
            }
        )

    telemetry = dict(result.telemetry or {})
    if result.normalization_meta:
        telemetry.setdefault("normalization", dict(result.normalization_meta))

    return _normalize_response_telemetry(
        LLMResponse(
            ok=result.error is None,
            provider=result.provider,
            model=result.model,
            output_text=result.output_text,
            assistant_messages=assistant_messages,
            tool_calls=list(result.tool_calls or []),
            thinking=[dict(item) for item in list(result.thinking or [])],
            usage=usage,
            latency_ms=max(0, int(result.latency_ms)),
            cost_usd=result.cost_usd,
            finish_reason=str(result.finish_reason or ""),
            provider_raw=result.provider_raw,
            error=result.error,
            telemetry=telemetry,
        )
    )


def coerce_provider_output(payload: ProviderOutput) -> LLMResponse:
    if isinstance(payload, LLMResponse):
        return _normalize_response_telemetry(payload)
    if isinstance(payload, ProviderAdapterResult):
        return adapter_result_to_llm_response(payload)
    if isinstance(payload, dict):
        try:
            return _normalize_response_telemetry(LLMResponse.model_validate(payload))
        except ValidationError:
            adapted = ProviderAdapterResult.model_validate(payload)
            return adapter_result_to_llm_response(adapted)
    return _normalize_response_telemetry(LLMResponse.model_validate(payload))
