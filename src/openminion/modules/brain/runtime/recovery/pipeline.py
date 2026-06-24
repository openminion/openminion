from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from openminion.base.time import utc_now_iso as _iso_now
from openminion.modules.llm.schemas import Message

from .schemas import (
    FailClosedReason,
    RepairType,
    RetryReason,
    TCRPAggregates,
    TCRPBudgetExhaustedEvent,
    TCRPRepairFiredEvent,
    TCRPRetryBudget,
    TCRPRetryEmittedEvent,
    TCRPStage,
    TCRPStageEvent,
    TCRPValidationError,
    TCRPValidationFailedEvent,
    ValidationErrorCode,
    error_code_from_pydantic,
    event_payload,
    retry_reason_for_error,
)

RepairPayload = dict[str, Any] | str | bytes
RepairFunction = Callable[..., tuple[RepairPayload, bool]]
REPAIR_REGISTRY: dict[RepairType, RepairFunction] = {}
_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_CURLY_QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)


def register_repair(
    repair_type: RepairType,
) -> Callable[[RepairFunction], RepairFunction]:
    def _decorator(func: RepairFunction) -> RepairFunction:
        REPAIR_REGISTRY[repair_type] = func
        return func

    return _decorator


@dataclass(frozen=True, slots=True)
class TCRPContext:
    channel_name: str
    trace_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    include_raw_payload: bool = False


@dataclass(slots=True)
class TCRPResult:
    channel_name: str
    normalized_payload: Any
    structured_payload: Any | None = None
    validation_errors: tuple[TCRPValidationError, ...] = ()
    retry_message: Message | None = None
    retry_reason: RetryReason | None = None
    should_retry: bool = False
    retries_consumed: int = 0
    fail_closed_reason: FailClosedReason | None = None
    events: tuple[TCRPStageEvent, ...] = ()


def _raw_size_bytes(payload: Any) -> int:
    if isinstance(payload, bytes):
        return len(payload)
    if isinstance(payload, str):
        return len(payload.encode("utf-8", errors="ignore"))
    if isinstance(payload, Mapping):
        try:
            return len(
                json.dumps(dict(payload), sort_keys=True, default=str).encode("utf-8")
            )
        except Exception:
            return len(str(dict(payload)).encode("utf-8", errors="ignore"))
    return len(str(payload).encode("utf-8", errors="ignore"))


def _base_event(
    ctx: TCRPContext,
    *,
    stage: TCRPStage,
    duration_ms: int,
) -> dict[str, Any]:
    return {
        "channel_name": ctx.channel_name,
        "stage": stage,
        "trace_id": ctx.trace_id,
        "session_id": ctx.session_id,
        "agent_id": ctx.agent_id,
        "timestamp": _iso_now(),
        "duration_ms": max(0, int(duration_ms)),
    }


def _emit_event(logger: Any | None, event_name: str, event: TCRPStageEvent) -> None:
    if logger is None or not hasattr(logger, "emit"):
        return
    try:
        logger.emit(
            event_name, event_payload(event), trace_id=str(event.trace_id or "")
        )
    except Exception:
        return


@register_repair(RepairType.SMART_QUOTE_NORMALIZE)
def repair_smart_quote_normalize(
    payload: RepairPayload,
    *,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
) -> tuple[RepairPayload, bool]:
    del alias_map, type_coercions, allow_code_fence
    if not isinstance(payload, str):
        return payload, False
    normalized = payload.translate(_CURLY_QUOTES)
    return normalized, normalized != payload


@register_repair(RepairType.WHITESPACE_NORMALIZE)
def repair_whitespace_normalize(
    payload: RepairPayload,
    *,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
) -> tuple[RepairPayload, bool]:
    del alias_map, type_coercions, allow_code_fence
    if isinstance(payload, bytes):
        decoded = payload.decode("utf-8", errors="replace").strip()
        return decoded, decoded.encode("utf-8", errors="ignore") != payload
    if isinstance(payload, str):
        normalized = payload.strip()
        return normalized, normalized != payload
    return payload, False


@register_repair(RepairType.CODE_FENCE_STRIP)
def repair_code_fence_strip(
    payload: RepairPayload,
    *,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
) -> tuple[RepairPayload, bool]:
    del alias_map, type_coercions
    if not allow_code_fence or not isinstance(payload, str):
        return payload, False
    match = _CODE_FENCE_RE.match(payload)
    if not match:
        return payload, False
    stripped = str(match.group(1) or "").strip()
    return stripped, stripped != payload


@register_repair(RepairType.TRAILING_COMMA)
def repair_trailing_comma(
    payload: RepairPayload,
    *,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
) -> tuple[RepairPayload, bool]:
    del alias_map, type_coercions, allow_code_fence
    if not isinstance(payload, str):
        return payload, False
    normalized = _TRAILING_COMMA_RE.sub(r"\1", payload)
    return normalized, normalized != payload


@register_repair(RepairType.STRINGIFIED_JSON)
def repair_stringified_json(
    payload: RepairPayload,
    *,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
) -> tuple[RepairPayload, bool]:
    del alias_map, type_coercions, allow_code_fence
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except Exception:
            return payload, False
    if not isinstance(payload, str):
        return payload, False
    raw = payload.strip()
    if not raw:
        return payload, False
    try:
        decoded = json.loads(raw)
    except Exception:
        return payload, False
    if isinstance(decoded, str):
        inner = decoded.strip()
        if inner.startswith("{") or inner.startswith("["):
            try:
                decoded_inner = json.loads(inner)
            except Exception:
                return decoded, True
            return decoded_inner, True
    return decoded, True


@register_repair(RepairType.FIELD_ALIAS)
def repair_field_alias(
    payload: RepairPayload,
    *,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
) -> tuple[RepairPayload, bool]:
    del type_coercions, allow_code_fence
    if not isinstance(payload, dict) or not alias_map:
        return payload, False
    normalized = dict(payload)
    changed = False
    for incoming_key, canonical_key in alias_map.items():
        if incoming_key not in normalized or canonical_key in normalized:
            continue
        normalized[canonical_key] = normalized.pop(incoming_key)
        changed = True
    return normalized, changed


@register_repair(RepairType.TYPE_COERCION)
def repair_type_coercion(
    payload: RepairPayload,
    *,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
) -> tuple[RepairPayload, bool]:
    del alias_map, allow_code_fence
    if not isinstance(payload, dict) or not type_coercions:
        return payload, False
    normalized = dict(payload)
    changed = False
    for key, coercer in type_coercions.items():
        if key not in normalized:
            continue
        raw = normalized[key]
        try:
            coerced = coercer(raw)
        except Exception:
            continue
        if coerced != raw:
            normalized[key] = coerced
            changed = True
    return normalized, changed


def normalize_payload(
    payload: Any,
    *,
    ctx: TCRPContext,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
    logger: Any | None = None,
) -> tuple[Any, tuple[TCRPStageEvent, ...]]:
    events: list[TCRPStageEvent] = []
    current: Any = payload
    for repair_type in RepairType:
        repair_fn = REPAIR_REGISTRY[repair_type]
        started = time.perf_counter()
        current, changed = repair_fn(
            current,
            alias_map=alias_map,
            type_coercions=type_coercions,
            allow_code_fence=allow_code_fence,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        if not changed:
            continue
        event = TCRPRepairFiredEvent(
            **_base_event(
                ctx,
                stage=TCRPStage.STRUCTURAL_NORMALIZATION,
                duration_ms=duration_ms,
            ),
            repair_type=repair_type,
            repair_succeeded=True,
            raw_size_bytes=_raw_size_bytes(payload),
        )
        events.append(event)
        _emit_event(logger, "brain.tcrp.repair_fired", event)
    return current, tuple(events)


def _typed_validation_errors(exc: ValidationError) -> tuple[TCRPValidationError, ...]:
    typed: list[TCRPValidationError] = []
    for item in list(exc.errors() or []):
        loc = item.get("loc", ())
        field_path = ".".join(str(part) for part in loc) or "<root>"
        error_code = error_code_from_pydantic(str(item.get("type", "") or ""))
        ctx = item.get("ctx")
        if error_code == ValidationErrorCode.OTHER and isinstance(ctx, Mapping):
            raw_error = ctx.get("error")
            raw_error_text = str(raw_error or "").strip().lower()
            if "required" in raw_error_text:
                error_code = ValidationErrorCode.MISSING_REQUIRED
        expected_type = ""
        if isinstance(ctx, Mapping):
            expected_type = str(
                ctx.get("expected") or ctx.get("class_name") or ctx.get("literal") or ""
            ).strip()
        if not expected_type:
            if error_code == ValidationErrorCode.MISSING_REQUIRED:
                expected_type = "required"
            elif error_code == ValidationErrorCode.TYPE_MISMATCH:
                expected_type = "typed_value"
            elif error_code == ValidationErrorCode.INVALID_LITERAL:
                expected_type = "allowed_literal"
            else:
                expected_type = "structured_value"
        actual_input = item.get("input")
        if error_code == ValidationErrorCode.MISSING_REQUIRED:
            actual_type = "missing"
        else:
            actual_type = (
                type(actual_input).__name__ if actual_input is not None else "missing"
            )
        typed.append(
            TCRPValidationError(
                field_path=field_path,
                error_code=error_code,
                expected_type=expected_type,
                actual_type=actual_type,
            )
        )
    if not typed:
        typed.append(
            TCRPValidationError(
                field_path="<root>",
                error_code=ValidationErrorCode.OTHER,
                expected_type="structured_value",
                actual_type="unknown",
            )
        )
    return tuple(typed)


def _deterministic_validation_message(
    error: TCRPValidationError,
) -> str:
    return (
        f"validation_error field={error.field_path} "
        f"code={error.error_code.value} "
        f"expected={error.expected_type} "
        f"actual={error.actual_type}"
    )


def build_retry_tool_message(
    *,
    tool_call_id: str | None,
    tool_name: str,
    validation_error: TCRPValidationError,
    command_id: str | None = None,
) -> Message:
    from openminion.modules.brain.loop.tools.messages import (
        action_result_to_tool_message,
    )
    from openminion.modules.brain.schemas import ActionError, ActionResult, new_uuid

    message_text = _deterministic_validation_message(validation_error)
    action_result = ActionResult(
        command_id=str(command_id or new_uuid()),
        status="retry",
        summary=message_text,
        error=ActionError(
            code=f"TCRP_{validation_error.error_code.value.upper()}",
            message=message_text,
            details={
                "field_path": validation_error.field_path,
                "error_code": validation_error.error_code.value,
                "expected_type": validation_error.expected_type,
                "actual_type": validation_error.actual_type,
            },
        ),
    )
    return action_result_to_tool_message(tool_call_id, tool_name, action_result)


def validate_payload(
    payload: Any,
    *,
    model: Any,
    ctx: TCRPContext,
    alias_map: Mapping[str, str] | None = None,
    type_coercions: Mapping[str, Callable[[Any], Any]] | None = None,
    allow_code_fence: bool = True,
    retry_budget: TCRPRetryBudget | None = None,
    current_retries: int = 0,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    logger: Any | None = None,
) -> TCRPResult:
    normalized, events = normalize_payload(
        payload,
        ctx=ctx,
        alias_map=alias_map,
        type_coercions=type_coercions,
        allow_code_fence=allow_code_fence,
        logger=logger,
    )
    started = time.perf_counter()
    try:
        if isinstance(normalized, (str, bytes, bytearray)) and hasattr(
            model, "model_validate_json"
        ):
            structured = model.model_validate_json(normalized)
        else:
            structured = model.model_validate(normalized)
    except ValidationError as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        typed_errors = _typed_validation_errors(exc)
        failure_event = TCRPValidationFailedEvent(
            **_base_event(ctx, stage=TCRPStage.VALIDATION, duration_ms=duration_ms),
            validation_error=typed_errors[0],
        )
        all_events: list[TCRPStageEvent] = [*events, failure_event]
        _emit_event(logger, "brain.tcrp.validation_failed", failure_event)
        budget = retry_budget or TCRPRetryBudget(channel_name=ctx.channel_name)
        if current_retries < int(budget.max_retries):
            retry_reason = retry_reason_for_error(typed_errors[0].error_code)
            retry_event = TCRPRetryEmittedEvent(
                **_base_event(ctx, stage=TCRPStage.RETRY_EMISSION, duration_ms=0),
                retry_count=current_retries + 1,
                retry_reason=retry_reason,
            )
            all_events.append(retry_event)
            _emit_event(logger, "brain.tcrp.retry_emitted", retry_event)
            retry_message = (
                build_retry_tool_message(
                    tool_call_id=tool_call_id,
                    tool_name=str(tool_name or ctx.channel_name),
                    validation_error=typed_errors[0],
                )
                if tool_name
                else None
            )
            return TCRPResult(
                channel_name=ctx.channel_name,
                normalized_payload=normalized,
                validation_errors=typed_errors,
                retry_message=retry_message,
                retry_reason=retry_reason,
                should_retry=True,
                retries_consumed=current_retries + 1,
                events=tuple(all_events),
            )
        budget_event = TCRPBudgetExhaustedEvent(
            **_base_event(ctx, stage=TCRPStage.BUDGET_ENFORCEMENT, duration_ms=0),
            budget_name=ctx.channel_name,
            retries_consumed=current_retries,
            fail_closed_reason=budget.fail_closed_reason,
        )
        all_events.append(budget_event)
        _emit_event(logger, "brain.tcrp.budget_exhausted", budget_event)
        return TCRPResult(
            channel_name=ctx.channel_name,
            normalized_payload=normalized,
            validation_errors=typed_errors,
            should_retry=False,
            retries_consumed=current_retries,
            fail_closed_reason=budget.fail_closed_reason,
            events=tuple(all_events),
        )
    duration_ms = int((time.perf_counter() - started) * 1000)
    validation_event = TCRPStageEvent(
        **_base_event(ctx, stage=TCRPStage.VALIDATION, duration_ms=duration_ms)
    )
    all_events = [*events, validation_event]
    _emit_event(logger, "brain.tcrp.validation_passed", validation_event)
    return TCRPResult(
        channel_name=ctx.channel_name,
        normalized_payload=normalized,
        structured_payload=structured,
        events=tuple(all_events),
    )


def aggregate_stage_events(
    events: list[TCRPStageEvent] | tuple[TCRPStageEvent, ...],
) -> TCRPAggregates:
    event_list = list(events or [])
    if not event_list:
        return TCRPAggregates()
    stage_counts: dict[str, int] = {}
    repair_counts: dict[str, int] = {}
    retries: list[int] = []
    validation_failures = 0
    fail_closed = 0
    for event in event_list:
        raw_stage = getattr(event, "stage", "") or ""
        stage_name = (
            raw_stage.value if isinstance(raw_stage, TCRPStage) else str(raw_stage)
        )
        stage_counts[stage_name] = stage_counts.get(stage_name, 0) + 1
        if isinstance(event, TCRPRepairFiredEvent):
            repair_key = event.repair_type.value
            repair_counts[repair_key] = repair_counts.get(repair_key, 0) + 1
        elif isinstance(event, TCRPValidationFailedEvent):
            validation_failures += 1
        elif isinstance(event, TCRPRetryEmittedEvent):
            retries.append(int(event.retry_count))
        elif isinstance(event, TCRPBudgetExhaustedEvent):
            fail_closed += 1
    raw_count = len(event_list)
    retries_sorted = sorted(retries)
    if retries_sorted:
        idx = max(
            0,
            min(len(retries_sorted) - 1, int(round(0.95 * (len(retries_sorted) - 1)))),
        )
        retry_p95 = retries_sorted[idx]
    else:
        retry_p95 = 0
    repair_total = sum(repair_counts.values())
    return TCRPAggregates(
        repair_rate=repair_total / raw_count,
        repair_type_distribution=repair_counts,
        validation_failure_rate=validation_failures / raw_count,
        retry_depth_p95=retry_p95,
        fail_closed_rate=fail_closed / raw_count,
        repair_rate_delta=0.0,
        raw_event_count=raw_count,
        event_counts_by_stage=stage_counts,
    )
