"""MCP 2026 request metadata and interactive-result handling."""

import json
import time
from typing import Any, Callable

from .constants import (
    MCP_CLIENT_NAME,
    MCP_CLIENT_VERSION,
    MCP_MAX_INPUT_ROUNDS,
    MCP_TASKS_GET_METHOD,
    MCP_TASKS_UPDATE_METHOD,
)
from .contracts import MCP_MODERN_PROTOCOL_VERSION


class MCPModernFlowError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MCPModernResponseCache:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, *, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        key = _response_cache_key(method=method, params=params)
        cached = self._entries.get(key)
        if cached is None:
            return None
        expires_at, result = cached
        if time.monotonic() >= expires_at:
            self._entries.pop(key, None)
            return None
        return dict(result)

    def store(
        self,
        *,
        method: str,
        params: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        ttl_ms = max(0, int(result.get("ttlMs", 0) or 0))
        if ttl_ms <= 0 or result.get("cacheScope") not in {"private", "public"}:
            return
        self._entries[_response_cache_key(method=method, params=params)] = (
            time.monotonic() + (ttl_ms / 1000.0),
            dict(result),
        )

    def clear(self) -> None:
        self._entries.clear()


def build_modern_client_meta(capabilities: dict[str, Any]) -> dict[str, Any]:
    declared = dict(capabilities)
    extensions = dict(declared.get("extensions", {}) or {})
    extensions["io.modelcontextprotocol/tasks"] = {}
    declared["extensions"] = extensions
    return {
        "io.modelcontextprotocol/protocolVersion": MCP_MODERN_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientInfo": {
            "name": MCP_CLIENT_NAME,
            "version": MCP_CLIENT_VERSION,
        },
        "io.modelcontextprotocol/clientCapabilities": declared,
    }


def select_modern_version(result: dict[str, Any]) -> str:
    versions = result.get("supportedVersions", [])
    if not isinstance(versions, list) or MCP_MODERN_PROTOCOL_VERSION not in versions:
        raise MCPModernFlowError(
            "MCP server does not advertise the 2026-07-28 protocol revision.",
            reason_code="mcp_modern_protocol_unavailable",
        )
    return MCP_MODERN_PROTOCOL_VERSION


def resolve_modern_result(
    *,
    method: str,
    params: dict[str, Any],
    result: dict[str, Any],
    request: Callable[[str, dict[str, Any]], dict[str, Any]],
    fulfill: Callable[[str, dict[str, Any]], dict[str, Any] | None],
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    current = dict(result)
    current_params = dict(params)
    for _round in range(MCP_MAX_INPUT_ROUNDS):
        result_type = str(current.get("resultType", "complete") or "complete")
        if result_type == "input_required":
            current_params = _input_retry_params(
                base=current_params,
                result=current,
                fulfill=fulfill,
            )
            current = request(method, current_params)
            continue
        if result_type == "task":
            return _drive_task(
                task=current,
                request=request,
                fulfill=fulfill,
                deadline=deadline,
            )
        return current
    raise MCPModernFlowError(
        f"MCP input-required flow exceeded {MCP_MAX_INPUT_ROUNDS} rounds.",
        reason_code="mcp_input_rounds_exceeded",
    )


def _drive_task(
    *,
    task: dict[str, Any],
    request: Callable[[str, dict[str, Any]], dict[str, Any]],
    fulfill: Callable[[str, dict[str, Any]], dict[str, Any] | None],
    deadline: float,
) -> dict[str, Any]:
    task_id = str(task.get("taskId", "") or "").strip()
    if not task_id:
        raise MCPModernFlowError(
            "MCP task result omitted taskId.",
            reason_code="mcp_task_id_missing",
        )
    current = dict(task)
    answered: set[str] = set()
    while True:
        status = str(current.get("status", "") or "").strip().lower()
        if status == "completed":
            result = current.get("result")
            if isinstance(result, dict):
                return dict(result)
            raise MCPModernFlowError(
                f"MCP task {task_id!r} completed without a result.",
                reason_code="mcp_task_result_missing",
            )
        if status in {"cancelled", "failed"}:
            message = str(current.get("statusMessage", "") or status).strip()
            raise MCPModernFlowError(
                f"MCP task {task_id!r} {status}: {message}",
                reason_code=f"mcp_task_{status}",
            )
        if time.monotonic() >= deadline:
            raise MCPModernFlowError(
                f"MCP task {task_id!r} did not complete before timeout.",
                reason_code="mcp_task_timeout",
            )
        if status == "input_required":
            responses = _input_responses(
                current.get("inputRequests"),
                fulfill=fulfill,
                answered=answered,
            )
            if responses:
                request(
                    MCP_TASKS_UPDATE_METHOD,
                    {"taskId": task_id, "inputResponses": responses},
                )
        poll_ms = max(0, int(current.get("pollIntervalMs", 0) or 0))
        if poll_ms:
            time.sleep(min(poll_ms / 1000.0, max(0.0, deadline - time.monotonic())))
        current = request(MCP_TASKS_GET_METHOD, {"taskId": task_id})


def _input_retry_params(
    *,
    base: dict[str, Any],
    result: dict[str, Any],
    fulfill: Callable[[str, dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    retry = dict(base)
    retry["inputResponses"] = _input_responses(
        result.get("inputRequests"),
        fulfill=fulfill,
        answered=set(),
    )
    if "requestState" in result:
        retry["requestState"] = result["requestState"]
    return retry


def _input_responses(
    raw_requests: Any,
    *,
    fulfill: Callable[[str, dict[str, Any]], dict[str, Any] | None],
    answered: set[str],
) -> dict[str, Any]:
    if not isinstance(raw_requests, dict):
        return {}
    responses: dict[str, Any] = {}
    for key, raw_request in raw_requests.items():
        request_key = str(key or "").strip()
        if (
            not request_key
            or request_key in answered
            or not isinstance(raw_request, dict)
        ):
            continue
        method = str(raw_request.get("method", "") or "").strip()
        params = raw_request.get("params", {}) or {}
        if not method or not isinstance(params, dict):
            continue
        response = fulfill(method, dict(params))
        responses[request_key] = dict(response or {})
        answered.add(request_key)
    return responses


def _response_cache_key(*, method: str, params: dict[str, Any]) -> str:
    cache_params = dict(params)
    cache_params.pop("_meta", None)
    encoded = json.dumps(cache_params, sort_keys=True, separators=(",", ":"))
    return f"{method}:{encoded}"


__all__ = [
    "MCPModernFlowError",
    "MCPModernResponseCache",
    "build_modern_client_meta",
    "resolve_modern_result",
    "select_modern_version",
]
