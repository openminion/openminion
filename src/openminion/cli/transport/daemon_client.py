from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openminion.cli.bootstrap.loader import load_config_with_path


@dataclass(frozen=True)
class DaemonEndpoint:
    config_path: str
    host: str
    port: int
    token: str = ""

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class DaemonStreamEvent:
    event: str
    data: object


_DEFAULT_DAEMON_PROBE_TIMEOUT_S = 1.5


def resolve_daemon_endpoint(config_path: str | None) -> DaemonEndpoint:
    from openminion.daemon import resolve_ipc_bind

    config, resolved = load_config_with_path(config_path)
    host, port = resolve_ipc_bind(config)
    token = str(config.runtime.ipc_token or "").strip()
    return DaemonEndpoint(
        config_path=str(resolved),
        host=host,
        port=port,
        token=token,
    )


def _normalize_config_path(config_path: str | None) -> str:
    raw = str(config_path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except (OSError, RuntimeError):
        return raw


def _daemon_config_matches(endpoint: DaemonEndpoint, payload: dict[str, Any]) -> bool:
    daemon_payload = payload.get("daemon")
    if not isinstance(daemon_payload, dict):
        return False
    remote_config_path = _normalize_config_path(daemon_payload.get("config_path"))
    expected_config_path = _normalize_config_path(endpoint.config_path)
    if not remote_config_path or not expected_config_path:
        return False
    return remote_config_path == expected_config_path


def probe_daemon_endpoint(
    endpoint: DaemonEndpoint,
    *,
    timeout_s: float = _DEFAULT_DAEMON_PROBE_TIMEOUT_S,
) -> tuple[str, dict[str, Any]]:
    try:
        status, payload = daemon_request(
            endpoint=endpoint,
            method="GET",
            path="/v1/health",
            timeout_s=timeout_s,
        )
    except Exception:
        return "unreachable", {}
    if status >= 500 or not isinstance(payload, dict):
        return "unreachable", payload if isinstance(payload, dict) else {}
    if _daemon_config_matches(endpoint, payload):
        return "ok", payload
    return "mismatch", payload


def daemon_request(
    *,
    endpoint: DaemonEndpoint,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    request = _build_daemon_request(
        endpoint=endpoint,
        method=method,
        path=path,
        payload=payload,
        accept="application/json",
    )

    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            raw_body = response.read().decode("utf-8")
            return int(response.status), _parse_json_response(raw_body)
    except HTTPError as exc:
        raw_body = exc.read().decode("utf-8") if exc.fp is not None else ""
        return int(exc.code), _parse_json_response(raw_body)
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        reason = str(getattr(exc, "reason", exc))
        raise RuntimeError(f"daemon request failed: {reason}") from exc


def daemon_stream_request(
    *,
    endpoint: DaemonEndpoint,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
    on_event: Callable[[DaemonStreamEvent], None] | None = None,
) -> tuple[int, dict[str, Any]]:
    request = _build_daemon_request(
        endpoint=endpoint,
        method=method,
        path=path,
        payload=payload,
        accept="text/event-stream",
    )
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            content_type = str(response.headers.get("Content-Type", "") or "").lower()
            if "text/event-stream" not in content_type:
                raw_body = response.read().decode("utf-8")
                return int(response.status), _parse_json_response(raw_body)
            return int(response.status), _parse_sse_response(
                response=response,
                on_event=on_event,
            )
    except HTTPError as exc:
        raw_body = exc.read().decode("utf-8") if exc.fp is not None else ""
        return int(exc.code), _parse_json_response(raw_body)
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        reason = str(getattr(exc, "reason", exc))
        raise RuntimeError(f"daemon request failed: {reason}") from exc


def _build_daemon_request(
    *,
    endpoint: DaemonEndpoint,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    accept: str,
) -> Request:
    normalized_method = method.upper().strip() or "GET"
    normalized_path = path if path.startswith("/") else f"/{path}"
    body: bytes | None = None
    headers = {"Accept": accept}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if endpoint.token:
        headers["X-IPC-Token"] = endpoint.token
    return Request(
        url=f"{endpoint.base_url}{normalized_path}",
        method=normalized_method,
        headers=headers,
        data=body,
    )


def daemon_is_reachable(
    endpoint: DaemonEndpoint,
    *,
    timeout_s: float = _DEFAULT_DAEMON_PROBE_TIMEOUT_S,
) -> bool:
    status, _payload = probe_daemon_endpoint(endpoint, timeout_s=timeout_s)
    return status == "ok"


def _parse_json_response(raw_body: str) -> dict[str, Any]:
    text = str(raw_body or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "invalid_json", "message": text}}
    if isinstance(parsed, dict):
        return parsed
    return {
        "ok": False,
        "error": {"code": "invalid_payload", "message": "non-object response"},
    }


def _decode_sse_event_payload(data_lines: list[str]) -> object:
    raw_data = "\n".join(data_lines).strip()
    if not raw_data:
        return {}
    try:
        return json.loads(raw_data)
    except json.JSONDecodeError:
        return {"message": raw_data}


def _build_sse_final_payload(
    *,
    error: dict[str, Any] | None,
    turn: dict[str, Any],
    meta: dict[str, Any],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    trace_id = str(meta.get("trace_id", "") or turn.get("trace_id", "")).strip()
    if trace_id and not turn.get("trace_id"):
        turn["trace_id"] = trace_id

    if error is not None:
        payload: dict[str, Any] = {"ok": False, "error": error}
        if trace_id:
            payload["trace_id"] = trace_id
        if chunks:
            payload["chunks"] = chunks
        return payload
    if turn:
        payload = {"ok": True, "turn": turn}
        if trace_id:
            payload["trace_id"] = trace_id
        if chunks:
            payload["chunks"] = chunks
        return payload
    return {
        "ok": False,
        "error": {
            "code": "missing_stream_response",
            "message": "daemon stream ended without a final response",
        },
        "trace_id": trace_id,
        "chunks": chunks,
    }


def _parse_sse_response(
    *,
    response: Any,
    on_event: Callable[[DaemonStreamEvent], None] | None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    turn: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] = []
    done_received = False
    event_name: str | None = None
    data_lines: list[str] = []

    def _dispatch() -> None:
        nonlocal event_name, data_lines, meta, turn, error, done_received
        if event_name is None and not data_lines:
            return
        name = str(event_name or "message").strip() or "message"
        payload = _decode_sse_event_payload(data_lines)
        stream_event = DaemonStreamEvent(event=name, data=payload)
        if callable(on_event):
            try:
                on_event(stream_event)
            except Exception:
                pass
        if name == "meta" and isinstance(payload, dict):
            meta = dict(payload)
        elif name == "chunk" and isinstance(payload, dict):
            chunks.append(dict(payload))
        elif name == "response" and isinstance(payload, dict):
            turn = dict(payload)
        elif name == "error" and isinstance(payload, dict):
            error = dict(payload)
        elif name == "done":
            done_received = True
        event_name = None
        data_lines = []

    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            _dispatch()
            if done_received:
                break
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.partition(":")[2].strip())
            continue
    _dispatch()

    return _build_sse_final_payload(error=error, turn=turn, meta=meta, chunks=chunks)
