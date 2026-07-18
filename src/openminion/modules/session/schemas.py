"""Typed contracts for explicit session continuation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.base.constants import STATE_KEY_WORKING

from .interfaces import SESSION_CONTINUATION_SCHEMA_VERSION

DEFAULT_CONTINUATION_TTL_SECONDS = 86_400
MAX_CONTINUATION_TTL_SECONDS = 604_800
MAX_CONTINUATION_REFS = 48
MAX_CONTINUATION_PROGRESS_ITEMS = 24
MAX_CONTINUATION_SUMMARY_CHARS = 4_000

_FORBIDDEN_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "credentials",
    "env",
    "environment",
    "password",
    "raw_arguments",
    "raw_output",
    "secret",
    "token",
    "tool_arguments",
    "tool_output",
    STATE_KEY_WORKING,
}


def _contains_forbidden_content(value: Any, *, path: str = "payload") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_KEYS or normalized.endswith("_secret"):
                return f"{path}.{key}"
            nested = _contains_forbidden_content(item, path=f"{path}.{key}")
            if nested:
                return nested
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nested = _contains_forbidden_content(item, path=f"{path}[{index}]")
            if nested:
                return nested
    elif isinstance(value, str):
        lowered = value.strip().lower()
        if lowered.startswith(("bearer ", "sk-", "-----begin private key")):
            return path
    return None


class ContinuationProgressItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1, max_length=256)
    status: str = Field(min_length=1, max_length=64)


class SessionContinuationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SESSION_CONTINUATION_SCHEMA_VERSION
    created_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    source_session_id: str = Field(min_length=1, max_length=256)
    source_latest_seq: int = Field(ge=0)
    source_agent_id: str = Field(min_length=1, max_length=256)
    target_agent_id: str = Field(min_length=1, max_length=256)
    binding_mode: Literal["local_session_store"] = "local_session_store"
    source_checkpoint_ref: str | None = Field(default=None, max_length=512)
    workspace_ref: str | None = Field(default=None, max_length=512)
    project_run_ref: str | None = Field(default=None, max_length=512)
    task_ref: str | None = Field(default=None, max_length=512)
    goal_ref: str | None = Field(default=None, max_length=512)
    phase: str | None = Field(default=None, max_length=128)
    cursor: int | None = Field(default=None, ge=0)
    termination_reason: str | None = Field(default=None, max_length=128)
    plan_steps: list[ContinuationProgressItem] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_PROGRESS_ITEMS
    )
    intents: list[ContinuationProgressItem] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_PROGRESS_ITEMS
    )
    unresolved_clarification_ids: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_PROGRESS_ITEMS
    )
    pending_input_refs: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_PROGRESS_ITEMS
    )
    session_work_summary: str = Field(
        default="", max_length=MAX_CONTINUATION_SUMMARY_CHARS
    )
    session_work_summary_ref: str | None = Field(default=None, max_length=512)
    memory_refs: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_REFS
    )
    artifact_refs: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_REFS
    )
    checkpoint_refs: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_REFS
    )
    project_refs: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_REFS
    )
    recent_event_refs: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_REFS
    )
    permission_refs: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_REFS
    )
    omitted_field_reasons: list[str] = Field(
        default_factory=list, max_length=MAX_CONTINUATION_REFS
    )
    redaction_summary: dict[str, int] = Field(default_factory=dict)
    owner_versions: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "unresolved_clarification_ids",
        "pending_input_refs",
        "memory_refs",
        "artifact_refs",
        "checkpoint_refs",
        "project_refs",
        "recent_event_refs",
        "permission_refs",
        "omitted_field_reasons",
        mode="before",
    )
    @classmethod
    def _normalize_refs(cls, value: Any) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for raw in list(value or []):
            item = str(raw or "").strip()
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @model_validator(mode="after")
    def _validate_contract(self) -> "SessionContinuationPayload":
        if self.schema_version != SESSION_CONTINUATION_SCHEMA_VERSION:
            raise ValueError("unsupported_continuation_schema")
        ttl_ms = self.expires_at_ms - self.created_at_ms
        if ttl_ms <= 0 or ttl_ms > MAX_CONTINUATION_TTL_SECONDS * 1_000:
            raise ValueError("invalid_continuation_expiry")
        forbidden_path = _contains_forbidden_content(self.model_dump(mode="python"))
        if forbidden_path:
            raise ValueError(f"continuation_forbidden_field:{forbidden_path}")
        return self


class ContinuationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: SessionContinuationPayload
    warnings: list[str] = Field(default_factory=list, max_length=MAX_CONTINUATION_REFS)


class SessionContinuationPacket(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    packet_id: str = Field(min_length=1)
    event_seq: int = Field(ge=1)
    event_timestamp: str = Field(min_length=1)
    source_session_id: str = Field(min_length=1)
    payload: SessionContinuationPayload

    @model_validator(mode="after")
    def _cross_check_lineage(self) -> "SessionContinuationPacket":
        if self.source_session_id != self.payload.source_session_id:
            raise ValueError("continuation_source_mismatch")
        if self.payload.source_latest_seq >= self.event_seq:
            raise ValueError("continuation_source_cutoff_invalid")
        return self


class ContinuationBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["previewed", "created"]
    preview: ContinuationPreview
    packet: SessionContinuationPacket | None = None
    reason_code: str | None = None


class ContinuationApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["applied", "already_applied", "rejected"]
    packet_id: str
    source_session_id: str | None = None
    target_session_id: str | None = None
    source_event_id: str | None = None
    target_event_id: str | None = None
    reason_code: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ContinuationError(ValueError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


__all__ = [
    "ContinuationApplyResult",
    "ContinuationBuildResult",
    "ContinuationError",
    "ContinuationPreview",
    "ContinuationProgressItem",
    "DEFAULT_CONTINUATION_TTL_SECONDS",
    "MAX_CONTINUATION_TTL_SECONDS",
    "SessionContinuationPacket",
    "SessionContinuationPayload",
]
