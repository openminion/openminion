"""MCP 2026 request metadata and interactive-result handling."""

import json
import threading
import time
from typing import Any, Callable

from .constants import (
    MCP_CLIENT_NAME,
    MCP_CLIENT_VERSION,
    MCP_MAX_INPUT_ROUNDS,
    MCP_MODERN_RESPONSE_CACHE_MAX_ENTRIES,
    MCP_TASKS_CANCEL_METHOD,
    MCP_TASKS_GET_METHOD,
    MCP_TASKS_UPDATE_METHOD,
)
from .contracts import MCP_MODERN_PROTOCOL_VERSION

_MCP_TASK_STATUSES = frozenset(
    {"working", "input_required", "completed", "failed", "cancelled"}
)


class MCPModernFlowError(RuntimeError):
    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MCPModernResponseCache:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get(
        self, *, method: str, params: dict[str, Any], identity: str = ""
    ) -> dict[str, Any] | None:
        key = _response_cache_key(method=method, params=params, identity=identity)
        with self._lock:
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
        identity: str = "",
    ) -> None:
        if "ttlMs" not in result:
            raise MCPModernFlowError(
                "MCP cacheable result omitted ttlMs.",
                reason_code="mcp_cache_ttl_invalid",
            )
        ttl_ms = _nonnegative_int(result, "ttlMs", "mcp_cache_ttl_invalid")
        cache_scope = result.get("cacheScope")
        if cache_scope not in {"private", "public"}:
            raise MCPModernFlowError(
                "MCP cacheScope must be 'private' or 'public'.",
                reason_code="mcp_cache_scope_invalid",
            )
        if ttl_ms <= 0:
            return
        key = _response_cache_key(method=method, params=params, identity=identity)
        with self._lock:
            if (
                key not in self._entries
                and len(self._entries) >= MCP_MODERN_RESPONSE_CACHE_MAX_ENTRIES
            ):
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (
                time.monotonic() + (ttl_ms / 1000.0),
                dict(result),
            )

    def clear(self) -> None:
        with self._lock:
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
        result_type = _require_result_type(current)
        if result_type == "input_required":
            current_params = _input_retry_params(
                base=current_params,
                result=current,
                fulfill=fulfill,
            )
            current = request(method, current_params)
            continue
        if result_type == "task":
            if method != "tools/call":
                raise MCPModernFlowError(
                    f"MCP method {method!r} cannot return a task result.",
                    reason_code="mcp_task_method_invalid",
                )
            current = _drive_task(
                task=current,
                request=request,
                fulfill=fulfill,
                deadline=deadline,
            )
            continue
        if result_type == "complete":
            return current
        raise MCPModernFlowError(
            f"MCP result has unsupported resultType {result_type!r}.",
            reason_code="mcp_result_type_unsupported",
        )
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
    raw_task_id = task.get("taskId")
    if not isinstance(raw_task_id, str) or not raw_task_id.strip():
        raise MCPModernFlowError(
            "MCP task result omitted a valid taskId.",
            reason_code="mcp_task_id_missing",
        )
    task_id = raw_task_id.strip()
    current = dict(task)
    answered: set[str] = set()
    while True:
        _validate_task_fields(current)
        current_task_id = current["taskId"].strip()
        if current_task_id != task_id:
            raise MCPModernFlowError(
                f"MCP task response changed taskId from {task_id!r} to {current_task_id!r}.",
                reason_code="mcp_task_id_mismatch",
            )
        status = current.get("status")
        if status not in _MCP_TASK_STATUSES:
            raise MCPModernFlowError(
                f"MCP task {task_id!r} returned invalid status {status!r}.",
                reason_code="mcp_task_status_invalid",
            )
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
            _require_complete_task_method_result(
                MCP_TASKS_CANCEL_METHOD,
                request(MCP_TASKS_CANCEL_METHOD, {"taskId": task_id}),
            )
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
                _require_complete_task_method_result(
                    MCP_TASKS_UPDATE_METHOD,
                    request(
                        MCP_TASKS_UPDATE_METHOD,
                        {"taskId": task_id, "inputResponses": responses},
                    ),
                )
        poll_ms = _nonnegative_int(
            current, "pollIntervalMs", "mcp_task_poll_interval_invalid"
        )
        if poll_ms:
            time.sleep(min(poll_ms / 1000.0, max(0.0, deadline - time.monotonic())))
        current = _require_complete_task_method_result(
            MCP_TASKS_GET_METHOD,
            request(MCP_TASKS_GET_METHOD, {"taskId": task_id}),
        )


def _require_complete_task_method_result(
    method: str, result: dict[str, Any]
) -> dict[str, Any]:
    if _require_result_type(result) != "complete":
        raise MCPModernFlowError(
            f"MCP task method {method!r} did not return a complete result.",
            reason_code="mcp_task_result_type_invalid",
        )
    return result


def _nonnegative_int(payload: dict[str, Any], key: str, reason_code: str) -> int:
    value = payload.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MCPModernFlowError(
            f"MCP {key} must be a non-negative integer.",
            reason_code=reason_code,
        )
    return int(value)


def _validate_task_fields(task: dict[str, Any]) -> None:
    task_id = task.get("taskId")
    if not isinstance(task_id, str) or not task_id.strip():
        raise MCPModernFlowError(
            "MCP task result omitted a valid taskId.",
            reason_code="mcp_task_id_missing",
        )
    for key in ("createdAt", "lastUpdatedAt"):
        if not isinstance(task.get(key), str) or not task[key].strip():
            raise MCPModernFlowError(
                f"MCP task omitted required {key}.",
                reason_code="mcp_task_metadata_invalid",
            )
    if "ttlMs" not in task:
        raise MCPModernFlowError(
            "MCP task omitted required ttlMs.",
            reason_code="mcp_task_metadata_invalid",
        )
    ttl = task["ttlMs"]
    if ttl is not None and (
        isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0
    ):
        raise MCPModernFlowError(
            "MCP task ttlMs must be null or a non-negative integer.",
            reason_code="mcp_task_metadata_invalid",
        )


def _input_retry_params(
    *,
    base: dict[str, Any],
    result: dict[str, Any],
    fulfill: Callable[[str, dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any]:
    has_requests = "inputRequests" in result
    has_state = "requestState" in result
    if not has_requests and not has_state:
        raise MCPModernFlowError(
            "MCP input-required result omitted inputRequests and requestState.",
            reason_code="mcp_input_required_payload_missing",
        )
    retry = dict(base)
    if has_requests:
        retry["inputResponses"] = _input_responses(
            result["inputRequests"],
            fulfill=fulfill,
            answered=set(),
        )
    if has_state:
        retry["requestState"] = result["requestState"]
    return retry


def _require_result_type(result: dict[str, Any]) -> str:
    result_type = str(result.get("resultType", "") or "").strip()
    if not result_type:
        raise MCPModernFlowError(
            "MCP modern result omitted resultType.",
            reason_code="mcp_result_type_missing",
        )
    return result_type


def _input_responses(
    raw_requests: Any,
    *,
    fulfill: Callable[[str, dict[str, Any]], dict[str, Any] | None],
    answered: set[str],
) -> dict[str, Any]:
    if not isinstance(raw_requests, dict):
        raise MCPModernFlowError(
            "MCP input-required result omitted inputRequests.",
            reason_code="mcp_input_requests_invalid",
        )
    responses: dict[str, Any] = {}
    for key, raw_request in raw_requests.items():
        request_key = str(key or "").strip()
        if request_key in answered:
            continue
        if not request_key or not isinstance(raw_request, dict):
            raise MCPModernFlowError(
                "MCP input-required result contains an invalid request.",
                reason_code="mcp_input_request_invalid",
            )
        method = str(raw_request.get("method", "") or "").strip()
        params = raw_request.get("params", {}) or {}
        if not method or not isinstance(params, dict):
            raise MCPModernFlowError(
                f"MCP input request {request_key!r} is malformed.",
                reason_code="mcp_input_request_invalid",
            )
        response = fulfill(method, dict(params))
        responses[request_key] = dict(response or {})
        answered.add(request_key)
    return responses


def _response_cache_key(
    *, method: str, params: dict[str, Any], identity: str = ""
) -> str:
    cache_params = dict(params)
    cache_params.pop("_meta", None)
    encoded = json.dumps(cache_params, sort_keys=True, separators=(",", ":"))
    return f"{identity}:{method}:{encoded}"


__all__ = [
    "MCPModernFlowError",
    "MCPModernResponseCache",
    "build_modern_client_meta",
    "resolve_modern_result",
    "select_modern_version",
]
