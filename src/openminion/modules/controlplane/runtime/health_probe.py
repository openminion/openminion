"""Loopback HTTP probe sidecar for controlplane operator surfaces."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_PROMETHEUS_TEXT_VERSION = (0, 0, 4)
_PROMETHEUS_TEXT_CONTENT_TYPE = (
    "text/plain; version={}.{}.{}".format(*_PROMETHEUS_TEXT_VERSION)
)


@dataclass(frozen=True)
class ControlPlaneHealthProbeConfig:
    host: str = "127.0.0.1"
    port: int = 9100
    allow_remote: bool = False
    bearer_token: str | None = None


class ControlPlaneHealthProbeSidecar:
    def __init__(
        self,
        *,
        config: ControlPlaneHealthProbeConfig | None = None,
        get_status: Callable[[], dict[str, Any]] | None = None,
        get_audit_health: Callable[[], dict[str, Any]] | None = None,
        probe_store: Callable[[], bool] | None = None,
        get_metrics: Callable[[], bytes] | None = None,
    ) -> None:
        self.config = config or ControlPlaneHealthProbeConfig()
        self._get_status = get_status or (lambda: {})
        self._get_audit_health = get_audit_health or (lambda: {"audit": {"healthy": True}})
        self._probe_store = probe_store or (lambda: True)
        self._get_metrics = get_metrics or (lambda: b"")
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        port = self._server.server_address[1] if self._server else self.config.port
        return {
            "ok": self._last_error is None,
            "pid_alive": self._thread is not None and self._thread.is_alive(),
            "host": self.config.host,
            "port": port,
            "last_error": self._last_error,
        }

    def start(self) -> dict[str, Any]:
        if self._thread is not None and self._thread.is_alive():
            return self.status()
        self._validate_bind_policy()
        handler_cls = self._handler_class()
        try:
            self._server = ThreadingHTTPServer(
                (self.config.host, int(self.config.port)), handler_cls
            )
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="controlplane-health-probe",
                daemon=True,
            )
            self._thread.start()
            self._last_error = None
        except OSError as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise
        return self.status()

    def stop(self, *, kill: bool = False) -> dict[str, Any]:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=0.1 if kill else 2.0)
        return self.status() | {"stopped": True}

    def readiness(self) -> tuple[bool, dict[str, Any]]:
        audit = _audit_payload(self._get_audit_health())
        store_ok = bool(self._probe_store())
        status = _redact(self._get_status())
        worker_ok = _workers_ready(status)
        ready = bool(audit.get("healthy", True)) and store_ok and worker_ok
        return ready, {
            "ready": ready,
            "audit": audit,
            "store": {"healthy": store_ok},
            "workers": {"healthy": worker_ok},
        }

    def _validate_bind_policy(self) -> None:
        host = str(self.config.host).strip().lower()
        if host in _LOOPBACK_HOSTS:
            return
        if not self.config.allow_remote or not str(self.config.bearer_token or "").strip():
            raise ValueError(
                "controlplane health probe remote bind requires allow_remote=true and bearer token"
            )

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        sidecar = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if not sidecar._authorized(self.headers.get("Authorization")):
                    self._write(401, {"ok": False, "error": "unauthorized"})
                    return
                if self.path == "/healthz":
                    self._write(200, {"ok": True})
                elif self.path == "/readyz":
                    ready, payload = sidecar.readiness()
                    self._write(200 if ready else 503, payload)
                elif self.path == "/status":
                    self._write(200, _redact(sidecar._get_status()))
                elif self.path == "/metrics":
                    self.send_response(200)
                    self.send_header("Content-Type", _PROMETHEUS_TEXT_CONTENT_TYPE)
                    self.end_headers()
                    self.wfile.write(sidecar._get_metrics())
                else:
                    self._write(404, {"ok": False, "error": "not_found"})

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _write(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return _Handler

    def _authorized(self, authorization: str | None) -> bool:
        token = str(self.config.bearer_token or "").strip()
        if not token:
            return True
        return str(authorization or "").strip() == f"Bearer {token}"


def _audit_payload(raw: dict[str, Any]) -> dict[str, Any]:
    audit = raw.get("audit", raw) if isinstance(raw, dict) else {}
    return {
        "healthy": bool(audit.get("healthy", True)),
        "failures": int(audit.get("failures", 0) or 0),
        "last_error": "redacted" if audit.get("last_error") else None,
    }


def _workers_ready(status: dict[str, Any]) -> bool:
    runtime = status.get("channel_runtime", {}) if isinstance(status, dict) else {}
    state = str(runtime.get("state", "running") or "running")
    if state in {"failed", "stopped"}:
        return False
    for channel in dict(runtime.get("channels") or {}).values():
        cstate = str(dict(channel).get("state", "running") or "running")
        if cstate in {"failed", "stopped"}:
            return False
    return True


def _redact(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    blocked = {"chat_key", "chat_id", "user_key", "user_id", "prompt", "raw_error"}
    blocked_fragments = ("token", "secret")

    def should_redact(key: object) -> bool:
        normalized = str(key).lower()
        return normalized in blocked or any(
            fragment in normalized for fragment in blocked_fragments
        )

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: ("redacted" if should_redact(k) else scrub(v))
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    return scrub(payload)


__all__ = ["ControlPlaneHealthProbeConfig", "ControlPlaneHealthProbeSidecar"]
