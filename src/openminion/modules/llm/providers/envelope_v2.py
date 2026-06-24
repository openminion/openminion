from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from openminion.base.errors.catalog import (
    DuplicateCallIdError as _CatalogDuplicateCallIdError,
)

CONTRACT_VERSION_V2 = "v2"
CONTRACT_MINOR_VERSION_V2_1 = "v2.1"

DEFAULT_EXECUTION_HINT = "auto"
DEFAULT_SOURCE = "native"
DEFAULT_RESULT_STATUS = "success"

ERROR_INVALID_ENVELOPE_SHAPE = "INVALID_ENVELOPE_SHAPE"
ERROR_INVALID_CALL_SHAPE = "INVALID_CALL_SHAPE"
ERROR_INVALID_RESULT_SHAPE = "INVALID_RESULT_SHAPE"
ERROR_INVALID_ENVELOPE_VERSION = "INVALID_ENVELOPE_VERSION"
ERROR_DUPLICATE_CALL_ID = "DUPLICATE_CALL_ID"


class EnvelopeParseError(ValueError):
    """Raised when a v2 envelope payload violates the contract."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: str = code
        self.message: str = message
        self.details: dict[str, Any] = dict(details or {})


class DuplicateCallIdParseError(EnvelopeParseError, _CatalogDuplicateCallIdError):
    """Typed `DUPLICATE_CALL_ID` parse-time error."""

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        EnvelopeParseError.__init__(
            self,
            ERROR_DUPLICATE_CALL_ID,
            message,
            details=details,
        )


@dataclass
class ToolCallV2:
    """Single tool call inside a `ToolCallEnvelopeV2`.

    Mirrors the per-call fields in contract sec 4.1.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    execution_hint: str = DEFAULT_EXECUTION_HINT
    source: str = DEFAULT_SOURCE


@dataclass
class ThinkingBlockV2:
    """Serialized reasoning block emitted between tool calls."""

    type: str = "thinking"
    content: str = ""
    signature: str | None = None
    redacted: bool = False


@dataclass
class ToolCallEnvelopeV2:
    """v2 tool-call envelope with optional thinking blocks."""

    request_id: str
    session_id: str
    turn_id: str
    calls: list[ToolCallV2] = field(default_factory=list)
    contract_version: str = CONTRACT_VERSION_V2
    thinking_blocks: list[ThinkingBlockV2] = field(default_factory=list)


@dataclass
class ToolResultV2:
    """Single tool result inside a `ToolResultEnvelopeV2`.

    Mirrors the per-result fields in contract sec 4.2.
    """

    id: str
    name: str
    ok: bool
    status: str = DEFAULT_RESULT_STATUS
    error_code: str = ""
    error_message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    duration_ms: int = 0


@dataclass
class ToolResultEnvelopeV2:
    """v2 tool-result envelope (contract sec 4.2)."""

    request_id: str
    session_id: str
    turn_id: str
    results: list[ToolResultV2] = field(default_factory=list)
    contract_version: str = CONTRACT_VERSION_V2


def parse_tool_call_envelope_v2(payload: Any) -> ToolCallEnvelopeV2:
    """Parse a v2 tool-call envelope from a plain dict.

    Raises `EnvelopeParseError` with a deterministic `code` on the first
    contract violation. See module docstring for the enforced rules.
    """

    payload_dict = _require_mapping(
        payload,
        what="tool-call envelope",
        error_code=ERROR_INVALID_ENVELOPE_SHAPE,
    )
    _require_contract_version(payload_dict, what="tool-call envelope")

    raw_calls = payload_dict.get("calls", [])
    if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
        raise EnvelopeParseError(
            ERROR_INVALID_ENVELOPE_SHAPE,
            "tool-call envelope: `calls` must be a list",
            details={"field": "calls", "type": type(raw_calls).__name__},
        )

    parsed_calls: list[ToolCallV2] = []
    seen_ids: set[str] = set()
    for index, raw_call in enumerate(raw_calls):
        call = _parse_tool_call_v2(raw_call, index=index)
        if call.id in seen_ids:
            raise DuplicateCallIdParseError(
                f"tool-call envelope: duplicate call id {call.id!r} at index {index}",
                details={"duplicate_call_id": call.id, "index": index},
            )
        seen_ids.add(call.id)
        parsed_calls.append(call)

    parsed_thinking = _parse_thinking_blocks(
        payload_dict.get("thinking_blocks", []),
        what="tool-call envelope",
    )

    return ToolCallEnvelopeV2(
        contract_version=str(payload_dict.get("contract_version", CONTRACT_VERSION_V2)),
        request_id=str(payload_dict.get("request_id", "")),
        session_id=str(payload_dict.get("session_id", "")),
        turn_id=str(payload_dict.get("turn_id", "")),
        calls=parsed_calls,
        thinking_blocks=parsed_thinking,
    )


def parse_tool_result_envelope_v2(payload: Any) -> ToolResultEnvelopeV2:
    """Parse a v2 tool-result envelope from a plain dict.

    Raises `EnvelopeParseError` on the first contract violation.
    """

    payload_dict = _require_mapping(
        payload,
        what="tool-result envelope",
        error_code=ERROR_INVALID_ENVELOPE_SHAPE,
    )
    _require_contract_version(payload_dict, what="tool-result envelope")

    raw_results = payload_dict.get("results", [])
    if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes)):
        raise EnvelopeParseError(
            ERROR_INVALID_ENVELOPE_SHAPE,
            "tool-result envelope: `results` must be a list",
            details={"field": "results", "type": type(raw_results).__name__},
        )

    parsed_results = [
        _parse_tool_result_v2(raw_result, index=index)
        for index, raw_result in enumerate(raw_results)
    ]

    return ToolResultEnvelopeV2(
        contract_version=str(payload_dict.get("contract_version", CONTRACT_VERSION_V2)),
        request_id=str(payload_dict.get("request_id", "")),
        session_id=str(payload_dict.get("session_id", "")),
        turn_id=str(payload_dict.get("turn_id", "")),
        results=parsed_results,
    )


def serialize_tool_call_envelope_v2(envelope: ToolCallEnvelopeV2) -> dict[str, Any]:
    """Serialize a `ToolCallEnvelopeV2` back to a transport dict."""

    out: dict[str, Any] = {
        "contract_version": envelope.contract_version,
        "request_id": envelope.request_id,
        "session_id": envelope.session_id,
        "turn_id": envelope.turn_id,
        "calls": [_serialize_tool_call_v2(call) for call in envelope.calls],
    }
    if envelope.thinking_blocks:
        out["thinking_blocks"] = [
            _serialize_thinking_block_v2(block) for block in envelope.thinking_blocks
        ]
    return out


def serialize_tool_result_envelope_v2(
    envelope: ToolResultEnvelopeV2,
) -> dict[str, Any]:
    """Serialize a `ToolResultEnvelopeV2` back to a plain dict for transport."""

    return {
        "contract_version": envelope.contract_version,
        "request_id": envelope.request_id,
        "session_id": envelope.session_id,
        "turn_id": envelope.turn_id,
        "results": [_serialize_tool_result_v2(result) for result in envelope.results],
    }


def _require_mapping(value: Any, *, what: str, error_code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EnvelopeParseError(
            error_code,
            f"{what}: payload must be an object/dict",
            details={"type": type(value).__name__},
        )
    return value


def _require_contract_version(payload: Mapping[str, Any], *, what: str) -> None:
    version = payload.get("contract_version")
    if version != CONTRACT_VERSION_V2:
        raise EnvelopeParseError(
            ERROR_INVALID_ENVELOPE_VERSION,
            f"{what}: contract_version must be {CONTRACT_VERSION_V2!r}",
            details={"contract_version": version},
        )


def _parse_tool_call_v2(raw: Any, *, index: int) -> ToolCallV2:
    if not isinstance(raw, Mapping):
        raise EnvelopeParseError(
            ERROR_INVALID_CALL_SHAPE,
            f"tool-call envelope: call at index {index} must be an object/dict",
            details={"index": index, "type": type(raw).__name__},
        )

    call_id = raw.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise EnvelopeParseError(
            ERROR_INVALID_CALL_SHAPE,
            f"tool-call envelope: call at index {index} is missing a non-empty `id`",
            details={"index": index, "field": "id"},
        )

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise EnvelopeParseError(
            ERROR_INVALID_CALL_SHAPE,
            f"tool-call envelope: call {call_id!r} is missing a non-empty `name`",
            details={"index": index, "field": "name", "id": call_id},
        )

    arguments = raw.get("arguments", {})
    if not isinstance(arguments, Mapping):
        raise EnvelopeParseError(
            ERROR_INVALID_CALL_SHAPE,
            f"tool-call envelope: call {call_id!r} `arguments` must be an object",
            details={
                "index": index,
                "field": "arguments",
                "id": call_id,
                "type": type(arguments).__name__,
            },
        )

    raw_deps = raw.get("depends_on", [])
    if not isinstance(raw_deps, Sequence) or isinstance(raw_deps, (str, bytes)):
        raise EnvelopeParseError(
            ERROR_INVALID_CALL_SHAPE,
            f"tool-call envelope: call {call_id!r} `depends_on` must be a list",
            details={
                "index": index,
                "field": "depends_on",
                "id": call_id,
                "type": type(raw_deps).__name__,
            },
        )
    deps: list[str] = []
    for dep_index, dep in enumerate(raw_deps):
        if not isinstance(dep, str) or not dep.strip():
            raise EnvelopeParseError(
                ERROR_INVALID_CALL_SHAPE,
                f"tool-call envelope: call {call_id!r} `depends_on[{dep_index}]` "
                f"must be a non-empty string",
                details={
                    "index": index,
                    "field": "depends_on",
                    "id": call_id,
                    "dep_index": dep_index,
                },
            )
        deps.append(dep)

    return ToolCallV2(
        id=call_id,
        name=name,
        arguments=dict(arguments),
        depends_on=deps,
        execution_hint=str(raw.get("execution_hint", DEFAULT_EXECUTION_HINT)),
        source=str(raw.get("source", DEFAULT_SOURCE)),
    )


def _parse_tool_result_v2(raw: Any, *, index: int) -> ToolResultV2:
    if not isinstance(raw, Mapping):
        raise EnvelopeParseError(
            ERROR_INVALID_RESULT_SHAPE,
            f"tool-result envelope: result at index {index} must be an object/dict",
            details={"index": index, "type": type(raw).__name__},
        )

    result_id = raw.get("id")
    if not isinstance(result_id, str) or not result_id.strip():
        raise EnvelopeParseError(
            ERROR_INVALID_RESULT_SHAPE,
            f"tool-result envelope: result at index {index} is missing a non-empty `id`",
            details={"index": index, "field": "id"},
        )

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise EnvelopeParseError(
            ERROR_INVALID_RESULT_SHAPE,
            f"tool-result envelope: result {result_id!r} is missing a non-empty `name`",
            details={"index": index, "field": "name", "id": result_id},
        )

    ok = raw.get("ok")
    if not isinstance(ok, bool):
        raise EnvelopeParseError(
            ERROR_INVALID_RESULT_SHAPE,
            f"tool-result envelope: result {result_id!r} `ok` must be a bool",
            details={
                "index": index,
                "field": "ok",
                "id": result_id,
                "type": type(ok).__name__,
            },
        )

    data = raw.get("data", {})
    if not isinstance(data, Mapping):
        raise EnvelopeParseError(
            ERROR_INVALID_RESULT_SHAPE,
            f"tool-result envelope: result {result_id!r} `data` must be an object",
            details={
                "index": index,
                "field": "data",
                "id": result_id,
                "type": type(data).__name__,
            },
        )

    return ToolResultV2(
        id=result_id,
        name=name,
        ok=ok,
        status=str(raw.get("status", DEFAULT_RESULT_STATUS)),
        error_code=str(raw.get("error_code", "")),
        error_message=str(raw.get("error_message", "")),
        data=dict(data),
        verified=bool(raw.get("verified", False)),
        duration_ms=int(raw.get("duration_ms", 0)),
    )


def _serialize_tool_call_v2(call: ToolCallV2) -> dict[str, Any]:
    return {
        "id": call.id,
        "name": call.name,
        "arguments": dict(call.arguments),
        "depends_on": list(call.depends_on),
        "execution_hint": call.execution_hint,
        "source": call.source,
    }


def _parse_thinking_blocks(
    raw: Any,
    *,
    what: str,
) -> list[ThinkingBlockV2]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise EnvelopeParseError(
            ERROR_INVALID_ENVELOPE_SHAPE,
            f"{what}: `thinking_blocks` must be a list",
            details={"field": "thinking_blocks", "type": type(raw).__name__},
        )
    parsed: list[ThinkingBlockV2] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise EnvelopeParseError(
                ERROR_INVALID_ENVELOPE_SHAPE,
                f"{what}: thinking_blocks[{index}] must be an object/dict",
                details={"field": "thinking_blocks", "index": index},
            )
        parsed.append(
            ThinkingBlockV2(
                type=str(item.get("type", "thinking")),
                content=str(item.get("content", "") or ""),
                signature=(
                    str(item["signature"])
                    if item.get("signature") is not None
                    else None
                ),
                redacted=bool(item.get("redacted", False)),
            )
        )
    return parsed


def _serialize_thinking_block_v2(block: ThinkingBlockV2) -> dict[str, Any]:
    """Runtime helper."""

    out: dict[str, Any] = {
        "type": block.type,
        "content": block.content,
        "redacted": block.redacted,
    }
    if block.signature is not None:
        out["signature"] = block.signature
    return out


def _serialize_tool_result_v2(result: ToolResultV2) -> dict[str, Any]:
    return {
        "id": result.id,
        "name": result.name,
        "ok": result.ok,
        "status": result.status,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "data": dict(result.data),
        "verified": result.verified,
        "duration_ms": result.duration_ms,
    }


__all__ = [
    "CONTRACT_VERSION_V2",
    "DEFAULT_EXECUTION_HINT",
    "DEFAULT_SOURCE",
    "DEFAULT_RESULT_STATUS",
    "ERROR_INVALID_ENVELOPE_SHAPE",
    "ERROR_INVALID_CALL_SHAPE",
    "ERROR_INVALID_RESULT_SHAPE",
    "ERROR_INVALID_ENVELOPE_VERSION",
    "ERROR_DUPLICATE_CALL_ID",
    "EnvelopeParseError",
    "ToolCallV2",
    "ToolCallEnvelopeV2",
    "ToolResultV2",
    "ToolResultEnvelopeV2",
    "parse_tool_call_envelope_v2",
    "parse_tool_result_envelope_v2",
    "serialize_tool_call_envelope_v2",
    "serialize_tool_result_envelope_v2",
]
