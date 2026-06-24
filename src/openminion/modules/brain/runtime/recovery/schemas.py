from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RepairType(str, Enum):
    STRINGIFIED_JSON = "stringified_json"
    TRAILING_COMMA = "trailing_comma"
    FIELD_ALIAS = "field_alias"
    TYPE_COERCION = "type_coercion"
    CODE_FENCE_STRIP = "code_fence_strip"
    SMART_QUOTE_NORMALIZE = "smart_quote_normalize"
    WHITESPACE_NORMALIZE = "whitespace_normalize"


class ValidationErrorCode(str, Enum):
    MISSING_REQUIRED = "missing_required"
    TYPE_MISMATCH = "type_mismatch"
    INVALID_LITERAL = "invalid_literal"
    EXTRA_FORBIDDEN = "extra_forbidden"
    MODEL_TYPE = "model_type"
    INVALID_JSON = "invalid_json"
    OTHER = "other"


class RetryReason(str, Enum):
    MISSING_REQUIRED = "missing_required"
    TYPE_MISMATCH = "type_mismatch"
    INVALID_LITERAL = "invalid_literal"
    EXTRA_FORBIDDEN = "extra_forbidden"
    MODEL_TYPE = "model_type"
    INVALID_JSON = "invalid_json"
    OTHER = "other"


class FailClosedReason(str, Enum):
    VALIDATION_BUDGET_EXHAUSTED = "validation_budget_exhausted"
    INVALID_PAYLOAD = "invalid_payload"
    PIPELINE_ERROR = "pipeline_error"


class TCRPStage(str, Enum):
    RAW_RECEIVE = "raw_receive"
    STRUCTURAL_NORMALIZATION = "structural_normalization"
    VALIDATION = "validation"
    RETRY_EMISSION = "retry_emission"
    BUDGET_ENFORCEMENT = "budget_enforcement"


class TCRPValidationError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str
    error_code: ValidationErrorCode
    expected_type: str
    actual_type: str


class TCRPRetryBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_name: str
    max_retries: int = Field(default=3, ge=0)
    fail_closed_reason: FailClosedReason = FailClosedReason.VALIDATION_BUDGET_EXHAUSTED


class TCRPStageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_name: str
    stage: TCRPStage
    schema_version: str = "tcrp.v1"
    trace_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    timestamp: str
    duration_ms: int = Field(default=0, ge=0)


class TCRPRepairFiredEvent(TCRPStageEvent):
    repair_type: RepairType
    repair_succeeded: bool
    raw_size_bytes: int = Field(default=0, ge=0)


class TCRPValidationFailedEvent(TCRPStageEvent):
    validation_error: TCRPValidationError


class TCRPRetryEmittedEvent(TCRPStageEvent):
    retry_count: int = Field(default=0, ge=0)
    retry_reason: RetryReason


class TCRPBudgetExhaustedEvent(TCRPStageEvent):
    budget_name: str
    retries_consumed: int = Field(default=0, ge=0)
    fail_closed_reason: FailClosedReason


class TCRPAggregates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repair_rate: float = 0.0
    repair_type_distribution: dict[str, int] = Field(default_factory=dict)
    validation_failure_rate: float = 0.0
    retry_depth_p95: int = 0
    fail_closed_rate: float = 0.0
    repair_rate_delta: float = 0.0
    raw_event_count: int = 0
    event_counts_by_stage: dict[str, int] = Field(default_factory=dict)


def retry_reason_for_error(code: ValidationErrorCode) -> RetryReason:
    return RetryReason(code.value)


def error_code_from_pydantic(error_type: str) -> ValidationErrorCode:
    token = str(error_type or "").strip().lower()
    if token == "missing":
        return ValidationErrorCode.MISSING_REQUIRED
    if token in {"int_parsing", "float_parsing", "bool_parsing", "string_type"}:
        return ValidationErrorCode.TYPE_MISMATCH
    if token in {"literal_error", "enum"}:
        return ValidationErrorCode.INVALID_LITERAL
    if token == "extra_forbidden":
        return ValidationErrorCode.EXTRA_FORBIDDEN
    if token == "model_type":
        return ValidationErrorCode.MODEL_TYPE
    if token == "json_invalid":
        return ValidationErrorCode.INVALID_JSON
    return ValidationErrorCode.OTHER


def event_stage_name(event: TCRPStageEvent) -> str:
    stage = getattr(event, "stage", "") or ""
    return stage.value if isinstance(stage, TCRPStage) else str(stage)


def event_payload(event: TCRPStageEvent) -> dict[str, Any]:
    return event.model_dump(mode="json")
