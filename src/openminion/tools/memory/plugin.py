"""Memory tool plugin."""

from dataclasses import asdict, is_dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext, resolve_memory_service
from openminion.modules.tool.contracts.runtime_ids import (
    RUNTIME_MEMORY_FORGET,
    RUNTIME_MEMORY_SEARCH,
    RUNTIME_MEMORY_WRITE,
)


class MemoryWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(..., min_length=1, description="Explicit memory scope")
    record_type: str = Field(
        ..., min_length=1, description="Explicit memory record type"
    )
    title: str = Field(..., min_length=1, description="Short record title")
    content: dict[str, Any] | str = Field(
        ..., description="Structured or literal content to store"
    )
    tags: list[str] = Field(default_factory=list, description="Optional string tags")
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Optional evidence refs already produced elsewhere",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional explicit confidence score",
    )

    @field_validator("scope", "record_type", "title", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("value is required")
        return token

    @field_validator("tags", "evidence_refs", mode="before")
    @classmethod
    def _normalize_string_lists(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("value must be a list")
        return [str(item).strip() for item in value if str(item or "").strip()]


class MemorySearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, description="Literal search query")
    scopes: list[str] = Field(
        ..., min_length=1, description="Explicit scopes to search"
    )
    types: list[str] = Field(default_factory=list, description="Optional record types")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum result count")

    @field_validator("query", mode="before")
    @classmethod
    def _normalize_query(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("query is required")
        return token

    @field_validator("scopes", "types", mode="before")
    @classmethod
    def _normalize_lists(cls, value: Any, info: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{info.field_name} must be a list")
        normalized = [str(item).strip() for item in value if str(item or "").strip()]
        if info.field_name == "scopes" and not normalized:
            raise ValueError("scopes must contain at least one value")
        return normalized


class MemoryForgetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(
        ..., min_length=1, description="Explicit record id to soft-delete"
    )

    @field_validator("record_id", mode="before")
    @classmethod
    def _normalize_record_id(cls, value: Any) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("record_id is required")
        return token


def _require_memory_service(ctx: RuntimeContext):
    service = resolve_memory_service(ctx)
    if service is None:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "memory tools are unavailable in this runtime",
            {"reason_code": "memory_service_unavailable"},
        )
    return service


def _serialize_record(record: Any) -> dict[str, Any]:
    payload = asdict(record) if is_dataclass(record) else dict(record)
    evidence_refs = payload.get("evidence_refs") or []
    payload["evidence_refs"] = [
        asdict(ref) if is_dataclass(ref) else dict(ref) for ref in evidence_refs
    ]
    return payload


def _h_memory_write(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    service = _require_memory_service(ctx)
    record_id = service.write_record(
        scope=str(args["scope"]),
        record_type=str(args["record_type"]),
        title=str(args["title"]),
        content=args["content"],
        tags=list(args.get("tags") or []),
        evidence_refs=list(args.get("evidence_refs") or []),
        confidence=args.get("confidence"),
    )
    return {
        "ok": True,
        "content": f"memory record stored: {record_id}",
        "data": {
            "record_id": record_id,
            "scope": str(args["scope"]),
            "record_type": str(args["record_type"]),
        },
    }


def _h_memory_search(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    service = _require_memory_service(ctx)
    query = str(args["query"])
    scopes = [str(item) for item in list(args["scopes"])]
    types = [str(item) for item in list(args.get("types") or [])]
    limit = int(args.get("limit") or 5)
    records = service.search(
        SearchQueryOptions(
            query=query,
            scopes=scopes,
            types=types or None,
            limit=limit,
        )
    )
    return {
        "ok": True,
        "content": f"memory search returned {len(records)} record(s)",
        "data": {
            "query": query,
            "scopes": scopes,
            "types": types,
            "count": len(records),
            "records": [_serialize_record(record) for record in records],
        },
    }


def _h_memory_forget(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    service = _require_memory_service(ctx)
    record_id = str(args["record_id"])
    deleted = bool(service.delete_record(record_id))
    return {
        "ok": deleted,
        "verified": deleted,
        "content": (
            f"memory record deleted: {record_id}"
            if deleted
            else f"memory record not found: {record_id}"
        ),
        "error": "" if deleted else "record_not_found",
        "data": {
            "record_id": record_id,
            "deleted": deleted,
        },
    }


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name="memory.write",
            args_model=MemoryWriteArgs,
            min_scope="WRITE_SAFE",
            handler=_h_memory_write,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "memory", "write"),
            capabilities=("memory", "write"),
            runtime_binding_id=RUNTIME_MEMORY_WRITE,
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name="memory.search",
            args_model=MemorySearchArgs,
            min_scope="READ_ONLY",
            handler=_h_memory_search,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "memory", "read"),
            capabilities=("memory", "read"),
            runtime_binding_id=RUNTIME_MEMORY_SEARCH,
        )
    )
    registry.add(
        ToolSpec(
            name="memory.forget",
            args_model=MemoryForgetArgs,
            min_scope="WRITE_SAFE",
            handler=_h_memory_forget,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "memory", "delete"),
            capabilities=("memory", "write"),
            runtime_binding_id=RUNTIME_MEMORY_FORGET,
            block_under_readonly=True,
        )
    )


__all__ = [
    "MemoryForgetArgs",
    "MemorySearchArgs",
    "MemoryWriteArgs",
    "register",
]
