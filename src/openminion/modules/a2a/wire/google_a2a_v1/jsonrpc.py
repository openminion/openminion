"""JSON-RPC 2.0 envelope handling for the A2A v1 wire adapter.

Spec: https://www.jsonrpc.org/specification — JSON-RPC 2.0 is the underlying
transport that the Google A2A v1 spec mandates for task submission.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass
from typing import Any

JSONRPC_VERSION = "2.0"
_JSONRPC_REQUEST_REQUIRED = ("jsonrpc", "method")


class JsonRpcErrorCode(enum.IntEnum):
    """Standard JSON-RPC 2.0 error codes plus A2A-specific extensions."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # A2A-specific (-32000 to -32099 reserved for server-defined errors per spec).
    TASK_NOT_FOUND = -32001
    TASK_REJECTED = -32002


@dataclass
class JsonRpcError:
    code: int
    message: str
    data: Any | None = None

    def to_jsonable(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": int(self.code), "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


@dataclass
class JsonRpcRequest:
    method: str
    params: dict[str, Any] | None = None
    id: str | int | None = None
    jsonrpc: str = JSONRPC_VERSION


@dataclass
class JsonRpcResponse:
    id: str | int | None
    result: Any | None = None
    error: JsonRpcError | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_jsonable(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            payload["error"] = self.error.to_jsonable()
        else:
            payload["result"] = self.result
        return payload


def parse_jsonrpc_request(body: str | bytes | dict[str, Any]) -> JsonRpcRequest:
    if isinstance(body, (str, bytes)):
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"PARSE_ERROR: {exc}") from exc
    elif isinstance(body, dict):
        payload = body
    else:
        raise ValueError(f"INVALID_REQUEST: unsupported body type {type(body)!r}")

    if not isinstance(payload, dict):
        raise ValueError("INVALID_REQUEST: body must be a JSON object")
    for required in _JSONRPC_REQUEST_REQUIRED:
        if required not in payload:
            raise ValueError(f"INVALID_REQUEST: missing required field {required!r}")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise ValueError(
            f"INVALID_REQUEST: jsonrpc field must be {JSONRPC_VERSION!r}, "
            f"got {payload.get('jsonrpc')!r}"
        )
    method = payload.get("method")
    if not isinstance(method, str) or not method.strip():
        raise ValueError("INVALID_REQUEST: method must be a non-empty string")

    params = payload.get("params")
    if params is not None and not isinstance(params, dict):
        raise ValueError("INVALID_REQUEST: params must be an object when present")

    return JsonRpcRequest(
        method=method,
        params=params,
        id=payload.get("id"),
        jsonrpc=JSONRPC_VERSION,
    )


def serialize_jsonrpc_response(response: JsonRpcResponse) -> str:
    return json.dumps(
        response.to_jsonable(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
