import json
from typing import Any

from openminion.modules.brain.adapters.llm.model_profiles import (
    RetryStrategy,
    resolve_capability_profile_for_context,
)
from openminion.modules.brain.interfaces import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    LLMAPI,
)
from openminion.modules.brain.retry import (
    STRUCTURED_FAILURE_KIND_KEY,
    STRUCTURED_HAS_TOOL_CALLS_KEY,
    STRUCTURED_RETRYABLE_KEY,
)
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.telemetry.trace.structured import write_structured_trace
from openminion.modules.telemetry.trace.phase_timing import (
    record_active_chat_provider_call,
)

from .normalize import _normalize_decision_submit_output_payload
from .normalize import _normalize_act_submit_output_payload
from .normalize import _normalize_plan_submit_output_payload
from .request import _build_request


def _validated_structured_payload(schema: type, payload: Any) -> Any:
    if callable(getattr(schema, "validate_python", None)):
        return schema.validate_python(payload)
    return schema.model_validate(payload)


def _serialize_validation_error(exc: Exception) -> list[dict[str, Any]]:
    if callable(getattr(exc, "errors", None)):
        try:
            errors = exc.errors()
        except Exception:
            errors = []
        if isinstance(errors, list):
            serialized: list[dict[str, Any]] = []
            for item in errors:
                if not isinstance(item, dict):
                    continue
                loc = item.get("loc")
                if isinstance(loc, (list, tuple)):
                    path = ".".join(str(part) for part in loc)
                else:
                    path = str(loc or "")
                serialized.append(
                    {
                        "path": path,
                        "category": str(item.get("type", "") or ""),
                        "message": str(item.get("msg", "") or ""),
                    }
                )
            if serialized:
                return serialized
    return [
        {
            "path": "",
            "category": exc.__class__.__name__,
            "message": str(exc),
        }
    ]


def _response_trace_context(response: Any) -> dict[str, Any] | None:
    telemetry = getattr(response, "telemetry", None)
    if isinstance(telemetry, dict):
        trace_context = telemetry.get("trace_context")
        if isinstance(trace_context, dict):
            return dict(trace_context)
    return None


def _restore_route_mode_alias(payload: Any) -> Any:
    if isinstance(payload, dict) and "route" in payload and "mode" not in payload:
        return {"mode": payload["route"], **payload}
    return payload


def _normalize_structured_submit_output_payload(
    *,
    schema_name: str,
    payload: Any,
    response: Any,
    return_debug: bool = False,
) -> Any:
    if schema_name == "Decision":
        return _normalize_decision_submit_output_payload(
            payload,
            response=response,
            return_debug=return_debug,
        )
    if schema_name == "_ActPayload":
        return _normalize_act_submit_output_payload(
            payload,
            response=response,
            return_debug=return_debug,
        )
    if schema_name == "Plan":
        return _normalize_plan_submit_output_payload(
            payload,
            response=response,
            return_debug=return_debug,
        )
    if return_debug:
        return payload, {"normalized_fields": [], "conflicts": []}
    return payload


def _extract_structured_output(
    response: Any,
    schema: type,
    *,
    extraction_chain: tuple[str, ...] = ("tool_calls", "json_body"),
    trace_context: dict[str, Any] | None = None,
) -> Any | None:
    schema_name = str(getattr(schema, "__name__", "") or "")
    attempts: list[dict[str, Any]] = []
    parsed: Any | None = None
    selected_strategy = ""

    for strategy_name in extraction_chain:
        strategy = str(strategy_name or "").strip()
        if strategy == "tool_calls":
            tool_calls = list(getattr(response, "tool_calls", []) or [])
            submit_call = next(
                (
                    item
                    for item in tool_calls
                    if str(getattr(item, "name", "") or "") == "submit_output"
                ),
                None,
            )
            if submit_call is None:
                attempts.append(
                    {
                        "strategy": "tool_calls",
                        "candidate_present": False,
                        "outcome": "no_candidate",
                        "tool_call_names": [
                            str(getattr(item, "name", "") or "") for item in tool_calls
                        ],
                    }
                )
                continue
            raw_arguments = getattr(submit_call, "arguments", {})
            if isinstance(raw_arguments, str):
                try:
                    raw_arguments = json.loads(raw_arguments)
                except Exception:
                    pass
            normalized_payload, normalization_debug = (
                _normalize_structured_submit_output_payload(
                    schema_name=schema_name,
                    payload=raw_arguments,
                    response=response,
                    return_debug=True,
                )
            )
            attempt = {
                "strategy": "tool_calls",
                "candidate_present": True,
                "outcome": "validation_failed",
                "tool_name": str(getattr(submit_call, "name", "") or ""),
                "tool_call_status": str(getattr(submit_call, "status", "") or ""),
                "normalized_fields": list(
                    normalization_debug.get("normalized_fields", [])
                ),
                "conflicts": list(normalization_debug.get("conflicts", [])),
            }
            try:
                validated = _validated_structured_payload(schema, normalized_payload)
            except Exception as exc:
                attempt["validation_errors"] = _serialize_validation_error(exc)
                attempts.append(attempt)
                continue
            attempt["outcome"] = "validated"
            attempts.append(attempt)
            if hasattr(validated, "model_dump"):
                parsed = _restore_route_mode_alias(validated.model_dump(mode="json"))
            else:
                parsed = validated
            selected_strategy = "tool_calls"
            break

        if strategy == "json_body":
            output_text = str(getattr(response, "output_text", "") or "").strip()
            if not output_text or output_text[:1] not in {"{", "["}:
                attempts.append(
                    {
                        "strategy": "json_body",
                        "candidate_present": False,
                        "outcome": "no_candidate",
                        "output_text_present": bool(output_text),
                    }
                )
                continue
            try:
                payload = json.loads(output_text)
            except Exception as exc:
                attempts.append(
                    {
                        "strategy": "json_body",
                        "candidate_present": True,
                        "outcome": "parse_failed",
                        "parse_error": str(exc),
                    }
                )
                continue
            normalized_payload, normalization_debug = (
                _normalize_structured_submit_output_payload(
                    schema_name=schema_name,
                    payload=payload,
                    response=response,
                    return_debug=True,
                )
            )
            attempt = {
                "strategy": "json_body",
                "candidate_present": True,
                "outcome": "validation_failed",
                "normalized_fields": list(
                    normalization_debug.get("normalized_fields", [])
                ),
                "conflicts": list(normalization_debug.get("conflicts", [])),
            }
            try:
                validated = _validated_structured_payload(schema, normalized_payload)
            except Exception as exc:
                attempt["validation_errors"] = _serialize_validation_error(exc)
                attempts.append(attempt)
                continue
            attempt["outcome"] = "validated"
            attempts.append(attempt)
            if hasattr(validated, "model_dump"):
                parsed = _restore_route_mode_alias(validated.model_dump(mode="json"))
            else:
                parsed = validated
            selected_strategy = "json_body"
            break

    if trace_context:
        write_structured_trace(
            trace_context=trace_context,
            patch={
                "schema_name": schema_name,
                "extraction_attempts": attempts,
                "selected_extraction_strategy": selected_strategy,
                "response_summary": {
                    "output_text_present": bool(
                        str(getattr(response, "output_text", "") or "").strip()
                    ),
                    "tool_call_count": len(
                        list(getattr(response, "tool_calls", []) or [])
                    ),
                },
            },
        )
    return parsed


def _invalid_decide_result(response: Any) -> dict[str, Any]:
    has_tool_calls = bool(getattr(response, "tool_calls", []) or [])
    reason_code = (
        "invalid_decide_tool_call"
        if has_tool_calls
        else "invalid_decide_structured_output"
    )
    return {
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: reason_code,
        STRUCTURED_HAS_TOOL_CALLS_KEY: has_tool_calls,
    }


def _invalid_structured_result(schema: type) -> dict[str, Any]:
    return {
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: "invalid_structured_output",
        "_structured_schema_name": str(getattr(schema, "__name__", "") or "structured"),
    }


def _requires_progressive_retry_for_empty_decide_answer(
    parsed: Any,
    *,
    profile: Any,
) -> bool:
    if (
        getattr(profile, "retry_strategy", "")
        != RetryStrategy.PROGRESSIVE_SIMPLIFICATION
    ):
        return False
    if not isinstance(parsed, dict):
        return False
    if str(parsed.get("route", parsed.get("mode", "")) or "").strip() != "respond":
        return False
    answer = parsed.get("answer")
    if answer is None:
        return True
    if isinstance(answer, str):
        return not answer.strip()
    return False


class LlmctlAdapter(LLMAPI):
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, client: Any) -> None:
        self.client = client
        self._last_trace_context: dict[str, Any] | None = None

    def get_last_trace_context(self) -> dict[str, Any] | None:
        return dict(self._last_trace_context) if self._last_trace_context else None

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model
        serialized = str(context or "")
        return max(1, len(serialized) // 4)

    def call_structured(
        self,
        *,
        model: str,
        purpose: str,
        context: dict[str, Any],
        schema: type,
        temperature: float = 0.0,
    ) -> Any:
        request = _build_request(
            model=model,
            purpose=purpose,
            context=context,
            schema=schema,
            temperature=temperature,
        )
        response = self.client.call(request)
        record_active_chat_provider_call(
            purpose=purpose,
            messages=list(request.messages),
            tools=list(request.tools or []),
            response=response,
        )
        if not response.ok:
            error = getattr(response, "error", None)
            if error is None:
                raise LLMCtlError(
                    "PROVIDER_ERROR",
                    "LLM call failed without provider error details",
                )
            raise LLMCtlError(
                str(getattr(error, "code", "") or "PROVIDER_ERROR"),
                str(getattr(error, "message", "") or "LLM call failed"),
                dict(getattr(error, "details", {}) or {}),
            )
        self._last_trace_context = _response_trace_context(response)

        profile = resolve_capability_profile_for_context(
            model_name=model,
            context=context,
        )
        parsed = _extract_structured_output(
            response,
            schema,
            extraction_chain=profile.extraction_chain,
            trace_context=self._last_trace_context,
        )
        if parsed is not None:
            if getattr(schema, "__name__", "") == "Decision" and isinstance(
                parsed, dict
            ):
                parsed[STRUCTURED_HAS_TOOL_CALLS_KEY] = bool(
                    getattr(response, "tool_calls", []) or []
                )
                if _requires_progressive_retry_for_empty_decide_answer(
                    parsed,
                    profile=profile,
                ):
                    if self._last_trace_context:
                        write_structured_trace(
                            trace_context=self._last_trace_context,
                            patch={
                                "failure_kind": "invalid_decide_missing_answer",
                                "has_tool_calls": bool(
                                    getattr(response, "tool_calls", []) or []
                                ),
                            },
                        )
                    return _invalid_decide_result(response)
            return parsed

        if self._last_trace_context:
            write_structured_trace(
                trace_context=self._last_trace_context,
                patch={
                    "failure_kind": (
                        "invalid_decide_tool_call"
                        if bool(getattr(response, "tool_calls", []) or [])
                        and getattr(schema, "__name__", "") == "Decision"
                        else "invalid_structured_output"
                    ),
                    "has_tool_calls": bool(getattr(response, "tool_calls", []) or []),
                },
            )
        if getattr(schema, "__name__", "") == "Decision":
            return _invalid_decide_result(response)
        return _invalid_structured_result(schema)
