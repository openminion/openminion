from __future__ import annotations

import hmac
import json
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import urlparse

from openminion.modules.a2a.errors import (
    A2AError,
    ERROR_CODE_AGENT_NOT_FOUND,
    ERROR_CODE_INTERNAL_ERROR,
    ERROR_CODE_INVALID_ARGUMENT,
    ERROR_CODE_INVALID_CONFIG,
    ERROR_CODE_JOB_NOT_FOUND,
    ERROR_CODE_NO_HANDLER,
    ERROR_CODE_POLICY_DENIED,
    ERROR_CODE_ROUTE_NOT_FOUND,
)
from openminion.modules.a2a.models import Envelope
from openminion.modules.a2a.runtime import A2ARuntime
from openminion.modules.a2a.wire.google_a2a_v1 import AgentSkill, build_agent_card

AGENT_CARD_PATH = "/.well-known/agent.json"

_HTTP_400_CODES = frozenset({ERROR_CODE_INVALID_ARGUMENT, ERROR_CODE_INVALID_CONFIG})
_HTTP_404_CODES = frozenset(
    {
        ERROR_CODE_AGENT_NOT_FOUND,
        ERROR_CODE_ROUTE_NOT_FOUND,
        ERROR_CODE_JOB_NOT_FOUND,
        ERROR_CODE_NO_HANDLER,
    }
)

# Raw exceptions the A2A runtime's storage/policy dependencies can surface when
# they fail. Kept as a narrow explicit tuple (never `Exception`) so the HTTP
# boundary stays typed and does not trip the broad-exception ratchet.
_RUNTIME_DEPENDENCY_ERRORS = (
    sqlite3.Error,
    ValueError,
    TypeError,
    KeyError,
    OSError,
    RuntimeError,
)


def _status_for_a2a_error(exc: A2AError) -> HTTPStatus:
    if exc.code in _HTTP_400_CODES:
        return HTTPStatus.BAD_REQUEST
    if exc.code == ERROR_CODE_POLICY_DENIED:
        return HTTPStatus.FORBIDDEN
    if exc.code in _HTTP_404_CODES:
        return HTTPStatus.NOT_FOUND
    return HTTPStatus.INTERNAL_SERVER_ERROR


class A2AHTTPRequestHandler(BaseHTTPRequestHandler):
    server_version = "OpenMinionA2A/0.1"

    @property
    def _server(self) -> "A2AHTTPServer":
        return cast("A2AHTTPServer", self.server)

    @property
    def _runtime(self) -> A2ARuntime:
        return self._server.runtime

    @property
    def _token(self) -> str:
        return self._server.token

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        path = urlparse(self.path).path
        if not self._authorized():
            return
        if path != AGENT_CARD_PATH:
            self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"Unknown path: {path}")
            return
        self._send_json(HTTPStatus.OK, self._build_agent_card())

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        path = urlparse(self.path).path
        if not self._authorized():
            return
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._send_error(
                HTTPStatus.BAD_REQUEST, ERROR_CODE_INVALID_ARGUMENT, str(exc)
            )
            return

        if path == "/a2a/call":
            self._handle_call(body)
            return
        if path == "/a2a/job/start":
            self._handle_job_start(body)
            return
        if path == "/a2a/job/status":
            self._handle_job_status(body)
            return
        if path == "/a2a/job/cancel":
            self._handle_job_cancel(body)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "NOT_FOUND", f"Unknown path: {path}")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    # -- auth -----------------------------------------------------------

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {self._token}"
        if not hmac.compare_digest(header, expected):
            self._send_error(
                HTTPStatus.UNAUTHORIZED, "UNAUTHORIZED", "Missing or invalid token"
            )
            return False
        return True

    # -- endpoints ------------------------------------------------------

    def _handle_call(self, body: dict[str, Any]) -> None:
        try:
            envelope = self._envelope_from_body(body)
        except A2AError as exc:
            self._send_error(
                _status_for_a2a_error(exc), exc.code, exc.message, exc.details
            )
            return
        try:
            result = self._runtime.call(envelope)
        except A2AError as exc:
            self._send_error(
                _status_for_a2a_error(exc), exc.code, exc.message, exc.details
            )
            return
        except _RUNTIME_DEPENDENCY_ERRORS as exc:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                ERROR_CODE_INTERNAL_ERROR,
                str(exc),
            )
            return
        self._send_json(HTTPStatus.OK, result.to_dict())

    def _handle_job_start(self, body: dict[str, Any]) -> None:
        try:
            envelope = self._envelope_from_body(body)
        except A2AError as exc:
            self._send_error(
                _status_for_a2a_error(exc), exc.code, exc.message, exc.details
            )
            return
        try:
            task_id = self._runtime.job_start(envelope)
        except A2AError as exc:
            self._send_error(
                _status_for_a2a_error(exc), exc.code, exc.message, exc.details
            )
            return
        except _RUNTIME_DEPENDENCY_ERRORS as exc:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                ERROR_CODE_INTERNAL_ERROR,
                str(exc),
            )
            return
        self._send_json(HTTPStatus.OK, {"task_id": task_id})

    def _handle_job_status(self, body: dict[str, Any]) -> None:
        task_id = self._require_task_id(body)
        if isinstance(task_id, A2AError):
            self._send_error(
                HTTPStatus.BAD_REQUEST, task_id.code, task_id.message, task_id.details
            )
            return
        try:
            record = self._runtime.job_status(task_id)
        except A2AError as exc:
            self._send_error(
                _status_for_a2a_error(exc), exc.code, exc.message, exc.details
            )
            return
        self._send_json(HTTPStatus.OK, record.to_dict())

    def _handle_job_cancel(self, body: dict[str, Any]) -> None:
        task_id = self._require_task_id(body)
        if isinstance(task_id, A2AError):
            self._send_error(
                HTTPStatus.BAD_REQUEST, task_id.code, task_id.message, task_id.details
            )
            return
        try:
            record = self._runtime.job_cancel(task_id)
        except A2AError as exc:
            self._send_error(
                _status_for_a2a_error(exc), exc.code, exc.message, exc.details
            )
            return
        self._send_json(HTTPStatus.OK, record.to_dict())

    # -- helpers --------------------------------------------------------

    def _envelope_from_body(self, body: dict[str, Any]) -> Envelope:
        if not isinstance(body, dict):
            raise A2AError(
                ERROR_CODE_INVALID_ARGUMENT, "Envelope body must be an object"
            )
        try:
            return Envelope.from_dict(body)
        except (TypeError, ValueError) as exc:
            raise A2AError(
                ERROR_CODE_INVALID_ARGUMENT, f"Invalid envelope: {exc}"
            ) from exc

    def _require_task_id(self, body: dict[str, Any]) -> str | A2AError:
        task_id = body.get("task_id") if isinstance(body, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            return A2AError(ERROR_CODE_INVALID_ARGUMENT, "'task_id' is required")
        return task_id

    def _read_json_body(self) -> dict[str, Any]:
        content_length_raw = self.headers.get("Content-Length", "0")
        try:
            content_length = int(content_length_raw)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length header") from exc
        if content_length <= 0:
            return {}
        raw_body = self.rfile.read(content_length).decode("utf-8")
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object")
        return parsed

    def _build_agent_card(self) -> dict[str, Any]:
        agents = self._runtime.list_agents()
        skills = [
            AgentSkill(
                id=str(item.get("agent_id", "")),
                name=str(item.get("agent_id", "")),
                description=", ".join(str(c) for c in item.get("capabilities", [])),
                tags=[str(t) for t in item.get("tags", [])],
            )
            for item in agents
            if isinstance(item, dict) and item.get("agent_id")
        ]
        card = build_agent_card(
            name="openminion-a2a",
            description="OpenMinion A2A cross-machine collaboration server.",
            url=self._origin(),
            version=self._runtime.contract_version,
            skills=skills,
        )
        payload = cast(dict[str, Any], card.to_jsonable())
        payload["agents"] = list(agents)
        return payload

    def _origin(self) -> str:
        host = self.headers.get("Host", "")
        if host:
            scheme = "https" if host.startswith("https://") else "http"
            return f"{scheme}://{host}"
        address = cast(tuple[str, int], self._server.server_address)
        return f"http://{address[0]}:{address[1]}"

    def _send_error(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        error: dict[str, Any] = {"code": code, "message": message}
        if details:
            error["details"] = dict(details)
        self._send_json(status, {"error": error})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class A2AHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        runtime: A2ARuntime,
        token: str,
    ) -> None:
        if not token or not token.strip():
            raise ValueError("A2A HTTP serving requires a non-empty token")
        self.runtime = runtime
        self.token = token
        super().__init__(server_address, A2AHTTPRequestHandler)


def serve_a2a(
    runtime: A2ARuntime,
    *,
    host: str = "127.0.0.1",
    port: int = 18790,
    token: str,
) -> None:
    """Block forever serving the given runtime over A2A HTTP with token auth."""
    server = A2AHTTPServer((host, port), runtime=runtime, token=token)
    try:
        server.serve_forever()
    finally:
        server.server_close()
