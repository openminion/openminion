from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any

from openminion.modules.telemetry.schemas import (
    TelemetryDebugDiagnostic,
    TelemetryDebugUsage,
)
from openminion.modules.telemetry.storage.base import (
    TelemetryEventPageRow,
    telemetry_event_sort_key,
)

_MAX_USAGE_INTEGER = 2**63 - 1


def _completed_calls(
    rows: list[TelemetryEventPageRow],
    diagnostics: list[TelemetryDebugDiagnostic],
) -> dict[str, TelemetryEventPageRow]:
    completed: dict[str, TelemetryEventPageRow] = {}
    facts: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.event.event_type != "llm.call.completed":
            continue
        call_id = str(row.event.data.get("llm_call_id") or "").strip()
        if not call_id:
            diagnostics.append(
                TelemetryDebugDiagnostic("UNCORRELATED_LLM_USAGE", "warning")
            )
            continue
        existing = completed.get(call_id)
        if existing is None or telemetry_event_sort_key(row) > telemetry_event_sort_key(
            existing
        ):
            completed[call_id] = row
        current = facts.get(call_id)
        if current is not None and current != row.event.data:
            diagnostics.append(
                TelemetryDebugDiagnostic(
                    "CONFLICTING_LLM_CALL_FACTS",
                    "warning",
                    {"llm_call_id": call_id},
                )
            )
        facts[call_id] = dict(row.event.data)
    return completed


def aggregate_debug_usage(
    rows: list[TelemetryEventPageRow],
    diagnostics: list[TelemetryDebugDiagnostic],
) -> TelemetryDebugUsage | None:
    completed = _completed_calls(rows, diagnostics)
    if not completed:
        return None

    sums: dict[str, int] = {}
    cost_sum: Decimal | None = None
    calls_with_usage = 0
    complete = True
    for call_id, row in completed.items():
        data = row.event.data
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        input_value, input_ok = _usage_alias(
            usage,
            "input_tokens",
            "prompt_tokens",
            call_id,
            diagnostics,
        )
        output_value, output_ok = _usage_alias(
            usage,
            "output_tokens",
            "completion_tokens",
            call_id,
            diagnostics,
        )
        total_value, total_ok = _usage_integer(
            usage.get("total_tokens"),
            call_id,
            "total_tokens",
            diagnostics,
        )
        if total_value is None and input_value is not None and output_value is not None:
            derived = input_value + output_value
            if derived <= _MAX_USAGE_INTEGER:
                total_value = derived
                total_ok = input_ok and output_ok
            else:
                _invalid_usage(call_id, "total_tokens", diagnostics)
                total_ok = False
        cost_value, cost_ok = _usage_cost(data, call_id, diagnostics)
        observed = any(
            value is not None
            for value in (input_value, output_value, total_value, cost_value)
        )
        if observed:
            calls_with_usage += 1
        complete = complete and all(
            (
                input_value is not None,
                output_value is not None,
                total_value is not None,
                input_ok,
                output_ok,
                total_ok,
                cost_ok,
            )
        )
        for name, value in (
            ("input_tokens", input_value),
            ("output_tokens", output_value),
            ("total_tokens", total_value),
        ):
            if value is None or name in sums and sums[name] > _MAX_USAGE_INTEGER:
                continue
            next_value = sums.get(name, 0) + value
            if next_value > _MAX_USAGE_INTEGER:
                sums[name] = _MAX_USAGE_INTEGER + 1
                _invalid_usage(call_id, name, diagnostics)
                complete = False
            else:
                sums[name] = next_value
        if cost_value is not None:
            cost_sum = (cost_sum or Decimal(0)) + cost_value
    values = {
        name: None if value > _MAX_USAGE_INTEGER else value
        for name, value in sums.items()
    }
    if not complete:
        diagnostics.append(TelemetryDebugDiagnostic("PARTIAL_USAGE", "warning"))
    return TelemetryDebugUsage(
        input_tokens=values.get("input_tokens"),
        output_tokens=values.get("output_tokens"),
        total_tokens=values.get("total_tokens"),
        cost_usd=_decimal_text(cost_sum) if cost_sum is not None else None,
        llm_call_count=len(completed),
        calls_with_usage=calls_with_usage,
        complete=complete,
    )


def _usage_alias(
    usage: dict[str, Any],
    preferred: str,
    alias: str,
    call_id: str,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> tuple[int | None, bool]:
    preferred_value, preferred_ok = _usage_integer(
        usage.get(preferred),
        call_id,
        preferred,
        diagnostics,
    )
    alias_value, alias_ok = _usage_integer(
        usage.get(alias),
        call_id,
        alias,
        diagnostics,
    )
    if preferred_value is not None and alias_value is not None:
        if preferred_value != alias_value:
            diagnostics.append(
                TelemetryDebugDiagnostic(
                    "CONFLICTING_USAGE_ALIASES",
                    "warning",
                    {"field": preferred, "llm_call_id": call_id},
                )
            )
            return preferred_value, False
        return preferred_value, preferred_ok and alias_ok
    if preferred_value is not None:
        return preferred_value, preferred_ok
    return alias_value, alias_ok


def _usage_integer(
    value: Any,
    call_id: str,
    field_name: str,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> tuple[int | None, bool]:
    if value is None:
        return None, True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid_usage(call_id, field_name, diagnostics)
        return None, False
    if isinstance(value, int):
        if value < 0 or value > _MAX_USAGE_INTEGER:
            _invalid_usage(call_id, field_name, diagnostics)
            return None, False
        return value, True
    number = float(value)
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        _invalid_usage(call_id, field_name, diagnostics)
        return None, False
    integer = int(number)
    if integer > _MAX_USAGE_INTEGER:
        _invalid_usage(call_id, field_name, diagnostics)
        return None, False
    return integer, True


def _usage_cost(
    data: dict[str, Any],
    call_id: str,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> tuple[Decimal | None, bool]:
    if data.get("cost_usd") is None:
        return None, True
    if not str(data.get("cost_source") or "").strip():
        return None, True
    try:
        value = Decimal(str(data["cost_usd"]))
    except (InvalidOperation, ValueError):
        _invalid_usage(call_id, "cost_usd", diagnostics)
        return None, False
    if not value.is_finite() or value < 0:
        _invalid_usage(call_id, "cost_usd", diagnostics)
        return None, False
    return value, True


def _invalid_usage(
    call_id: str,
    field_name: str,
    diagnostics: list[TelemetryDebugDiagnostic],
) -> None:
    diagnostics.append(
        TelemetryDebugDiagnostic(
            "INVALID_USAGE_FACT",
            "warning",
            {"field": field_name, "llm_call_id": call_id},
        )
    )


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = ["aggregate_debug_usage"]
