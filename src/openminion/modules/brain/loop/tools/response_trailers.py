from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from openminion.modules.task.plan import (
    TaskPlan,
    TaskPlanRevision,
    TaskPlanStepBlocked,
    TaskPlanStepCompleted,
    TaskPlanTerminalSignal,
)
from openminion.modules.llm.schemas import LLMResponse

TYPED_SIGNAL_SOURCES_TELEMETRY_KEY = "typed_signal_sources"
TYPED_SIGNAL_SOURCE_STRUCTURED_FIELD = "structured_field"
TYPED_SIGNAL_SOURCE_TRAILER = "trailer"

_TASK_PLAN_RE = re.compile(
    r"(?s)(?P<prefix>.*?)(?:\n\s*)?<task_plan>\s*(?P<payload>\{.*\})\s*</task_plan>\s*(?P<suffix>.*?)$"
)
_STEP_COMPLETED_RE = re.compile(
    r"(?s)(?P<prefix>.*?)(?:\n\s*)?<step_completed>\s*(?P<payload>\{.*\})\s*</step_completed>\s*(?P<suffix>.*?)$"
)
_STEP_BLOCKED_RE = re.compile(
    r"(?s)(?P<prefix>.*?)(?:\n\s*)?<step_blocked>\s*(?P<payload>\{.*\})\s*</step_blocked>\s*(?P<suffix>.*?)$"
)
_PLAN_REVISION_RE = re.compile(
    r"(?s)(?P<prefix>.*?)(?:\n\s*)?<plan_revision>\s*(?P<payload>\{.*\})\s*</plan_revision>\s*(?P<suffix>.*?)$"
)
_PLAN_ABANDONED_RE = re.compile(
    r"(?s)(?P<prefix>.*?)(?:\n\s*)?<plan_abandoned>\s*(?P<payload>\{.*\})\s*</plan_abandoned>\s*(?P<suffix>.*?)$"
)
_PLAN_COMPLETED_RE = re.compile(
    r"(?s)(?P<prefix>.*?)(?:\n\s*)?<plan_completed>\s*(?P<payload>\{.*\})\s*</plan_completed>\s*(?P<suffix>.*?)$"
)


def with_typed_signal_source(
    response: LLMResponse,
    *,
    field_name: str,
    source: str,
    update: dict[str, Any],
) -> LLMResponse:
    telemetry = dict(getattr(response, "telemetry", None) or {})
    sources = dict(telemetry.get(TYPED_SIGNAL_SOURCES_TELEMETRY_KEY) or {})
    sources[field_name] = source
    telemetry[TYPED_SIGNAL_SOURCES_TELEMETRY_KEY] = sources
    return response.model_copy(update={**update, "telemetry": telemetry})


def _replace_last_assistant_text(
    response: LLMResponse,
    *,
    stripped_text: str,
    update: dict[str, Any],
    signal_field_name: str | None = None,
    signal_source: str | None = None,
) -> LLMResponse:
    assistant_messages = list(getattr(response, "assistant_messages", []) or [])
    if assistant_messages:
        updated_messages = list(assistant_messages)
        last = updated_messages[-1]
        if getattr(last, "role", "") == "assistant":
            updated_messages[-1] = last.model_copy(update={"content": stripped_text})
        assistant_messages = updated_messages
    response_update = {
        "output_text": stripped_text,
        "assistant_messages": assistant_messages,
        **update,
    }
    if signal_field_name and signal_source:
        return with_typed_signal_source(
            response,
            field_name=signal_field_name,
            source=signal_source,
            update=response_update,
        )
    return response.model_copy(update=response_update)


def _normalize_model_trailer_response(
    response: LLMResponse,
    *,
    field_name: str,
    pattern: re.Pattern[str],
    model: type[Any],
) -> LLMResponse:
    existing = getattr(response, field_name, None)
    if isinstance(existing, dict):
        try:
            structured = model.model_validate(existing)
        except (ValidationError, json.JSONDecodeError):
            structured = None
        if structured is not None:
            return with_typed_signal_source(
                response,
                field_name=field_name,
                source=TYPED_SIGNAL_SOURCE_STRUCTURED_FIELD,
                update={field_name: structured.model_dump(mode="json")},
            )

    raw_text = str(getattr(response, "output_text", "") or "")
    if not raw_text:
        return response
    match = pattern.match(raw_text)
    if match is None:
        return response
    try:
        structured = model.model_validate(json.loads(match.group("payload")))
    except (ValidationError, json.JSONDecodeError):
        return response
    groups = match.groupdict()
    if "body" in groups:
        stripped_text = str(groups.get("body") or "").rstrip()
    else:
        prefix = str(groups.get("prefix") or "").rstrip()
        suffix = str(groups.get("suffix") or "").lstrip()
        stripped_text = f"{prefix}\n\n{suffix}".strip() if suffix else prefix
    return _replace_last_assistant_text(
        response,
        stripped_text=stripped_text,
        update={field_name: structured.model_dump(mode="json")},
        signal_field_name=field_name,
        signal_source=TYPED_SIGNAL_SOURCE_TRAILER,
    )


def _normalize_task_plan_response(response: LLMResponse) -> LLMResponse:
    return _normalize_model_trailer_response(
        response,
        field_name="task_plan",
        pattern=_TASK_PLAN_RE,
        model=TaskPlan,
    )


def _normalize_step_completed_response(response: LLMResponse) -> LLMResponse:
    return _normalize_model_trailer_response(
        response,
        field_name="task_plan_step_completed",
        pattern=_STEP_COMPLETED_RE,
        model=TaskPlanStepCompleted,
    )


def _normalize_step_blocked_response(response: LLMResponse) -> LLMResponse:
    return _normalize_model_trailer_response(
        response,
        field_name="task_plan_step_blocked",
        pattern=_STEP_BLOCKED_RE,
        model=TaskPlanStepBlocked,
    )


def _normalize_plan_revision_response(response: LLMResponse) -> LLMResponse:
    return _normalize_model_trailer_response(
        response,
        field_name="task_plan_revision",
        pattern=_PLAN_REVISION_RE,
        model=TaskPlanRevision,
    )


def _normalize_plan_abandoned_response(response: LLMResponse) -> LLMResponse:
    return _normalize_model_trailer_response(
        response,
        field_name="task_plan_abandoned",
        pattern=_PLAN_ABANDONED_RE,
        model=TaskPlanTerminalSignal,
    )


def _normalize_plan_completed_response(response: LLMResponse) -> LLMResponse:
    return _normalize_model_trailer_response(
        response,
        field_name="task_plan_completed",
        pattern=_PLAN_COMPLETED_RE,
        model=TaskPlanTerminalSignal,
    )


def normalize_task_plan_trailer_response(response: LLMResponse) -> LLMResponse:
    normalizers = (
        _normalize_plan_completed_response,
        _normalize_plan_abandoned_response,
        _normalize_plan_revision_response,
        _normalize_step_blocked_response,
        _normalize_step_completed_response,
        _normalize_task_plan_response,
    )
    current = response
    for _ in range(len(normalizers)):
        before = str(getattr(current, "output_text", "") or "")
        for normalize in normalizers:
            current = normalize(current)
        after = str(getattr(current, "output_text", "") or "")
        if after == before:
            break
    return current
