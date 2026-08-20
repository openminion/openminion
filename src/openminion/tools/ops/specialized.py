from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SENSITIVE_KEYS = ("secret", "token", "password", "credential", "key")


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScopedTargetArgs(StrictArgs):
    target_id: str = Field(min_length=1)
    scope: str = Field(default="fixture", min_length=1)


class TimeWindowArgs(ScopedTargetArgs):
    since: str = Field(
        default="", description="Inclusive lower bound or provider-relative time."
    )
    until: str = Field(
        default="", description="Exclusive upper bound or provider-relative time."
    )
    limit: int = Field(default=50, ge=1, le=500)


def fixture_data(ctx: Any, family_id: str) -> Mapping[str, Any]:
    extras = getattr(ctx, "extras", None)
    if not isinstance(extras, Mapping):
        return {}
    grouped = extras.get("ops_family_fixtures")
    if isinstance(grouped, Mapping):
        family_fixture = grouped.get(family_id)
        if isinstance(family_fixture, Mapping):
            return family_fixture
    direct = extras.get(f"{family_id}_fixture")
    return direct if isinstance(direct, Mapping) else {}


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if any(token in str(key).lower() for token in SENSITIVE_KEYS)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value


def evidence_result(
    *,
    family_id: str,
    operation: str,
    args: BaseModel,
    ctx: Any,
) -> dict[str, Any]:
    fixture = fixture_data(ctx, family_id)
    source = "fixture" if fixture else "unconfigured"
    raw_payload = fixture.get(operation, {}) if isinstance(fixture, Mapping) else {}
    payload = redact(raw_payload)
    serialized = repr((family_id, operation, args.model_dump(mode="json"), payload))
    evidence = {
        "family_id": family_id,
        "operation": operation,
        "source": source,
        "claim_status": "observed" if fixture else "unknown",
        "evidence_digest": hashlib.sha256(serialized.encode()).hexdigest(),
    }
    return {
        "ok": bool(fixture),
        "verified": bool(fixture),
        "content": f"{family_id}:{operation} {evidence['claim_status']}",
        "data": {
            "target": args.model_dump(mode="json"),
            "evidence": evidence,
            "result": payload,
        },
    }


def live_evidence_result(
    *,
    family_id: str,
    operation: str,
    args: BaseModel,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    sanitized = redact(payload)
    serialized = repr((family_id, operation, args.model_dump(mode="json"), sanitized))
    return {
        "ok": True,
        "verified": True,
        "content": f"{family_id}:{operation} observed",
        "data": {
            "target": args.model_dump(mode="json"),
            "evidence": {
                "family_id": family_id,
                "operation": operation,
                "source": "live",
                "claim_status": "observed",
                "evidence_digest": hashlib.sha256(serialized.encode()).hexdigest(),
            },
            "result": sanitized,
        },
    }


def make_handler(
    family_id: str, operation: str, args_model: type[BaseModel]
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        parsed = args_model.model_validate(args)
        return evidence_result(
            family_id=family_id,
            operation=operation,
            args=parsed,
            ctx=ctx,
        )

    return handler


def make_live_handler(
    family_id: str,
    operation: str,
    args_model: type[BaseModel],
    live_handler: Callable[[Any, Any], Mapping[str, Any]],
) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    fixture_handler = make_handler(family_id, operation, args_model)

    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        parsed = args_model.model_validate(args)
        if str(getattr(parsed, "scope", "fixture")) != "live":
            return fixture_handler(args, ctx)
        return live_evidence_result(
            family_id=family_id,
            operation=operation,
            args=parsed,
            payload=live_handler(parsed, ctx),
        )

    return handler
