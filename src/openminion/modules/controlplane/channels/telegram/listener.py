import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import urlparse

from openminion.modules.controlplane.channels.telegram.constants import (
    WEBHOOK_LISTENER_DEFAULT_PATH,
    WEBHOOK_LISTENER_MAX_BODY_BYTES,
    WEBHOOK_LISTENER_SECRET_HEADER,
)

_LOG = logging.getLogger(__name__)


class WebhookDispatchTarget(Protocol):
    """Subset of ``TelegramWebhookRunner`` the listener calls.

    Defined as a Protocol so tests can pass a minimal stub without
    standing up the full runner.
    """

    def handle_webhook_update(
        self,
        update: dict[str, Any],
        secret_token: object | None = None,
    ) -> dict[str, Any]: ...


def _resolve_route_path(configured_url: str | None) -> str:
    """Derive the listener route from ``WebhookConfig.url``.

    If ``url`` is set and parses with a non-empty path, return that path.
    Otherwise return the default ``/telegram/webhook``.
    """
    if not configured_url:
        return WEBHOOK_LISTENER_DEFAULT_PATH
    try:
        parsed = urlparse(str(configured_url))
    except (TypeError, ValueError):
        return WEBHOOK_LISTENER_DEFAULT_PATH
    path = (parsed.path or "").strip()
    if not path or path == "/":
        return WEBHOOK_LISTENER_DEFAULT_PATH
    return path


def _build_handler_class(
    *,
    route_path: str,
    runner: WebhookDispatchTarget,
    log: logging.Logger,
) -> type[BaseHTTPRequestHandler]:
    class _WebhookHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_POST(self) -> None:  # noqa: N802 (stdlib API)
            parsed = urlparse(self.path)
            if parsed.path != route_path:
                self._write_json(
                    HTTPStatus.NOT_FOUND,
                    {"success": False, "error": "not_found"},
                )
                return

            length_header = self.headers.get("Content-Length")
            try:
                content_length = int(length_header) if length_header else 0
            except ValueError:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"success": False, "error": "invalid_content_length"},
                )
                return

            if content_length < 0 or content_length > WEBHOOK_LISTENER_MAX_BODY_BYTES:
                self._write_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"success": False, "error": "payload_too_large"},
                )
                return

            raw = self.rfile.read(content_length) if content_length else b""
            try:
                update = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "success": False,
                        "error": "invalid_json",
                        "reason": str(exc),
                    },
                )
                return
            if not isinstance(update, dict):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"success": False, "error": "invalid_body_shape"},
                )
                return

            secret_token = self.headers.get(WEBHOOK_LISTENER_SECRET_HEADER)

            try:
                result = runner.handle_webhook_update(update, secret_token=secret_token)
            except Exception as exc:  # noqa: BLE001
                log.exception("webhook listener dispatch failed: %s", exc)
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"success": False, "error": "dispatch_failed"},
                )
                return

            if isinstance(result, dict) and result.get("error") == "unauthorized":
                self._write_json(HTTPStatus.UNAUTHORIZED, result)
                return
            self._write_json(HTTPStatus.OK, result if isinstance(result, dict) else {})

        def do_GET(self) -> None:  # noqa: N802 (stdlib API)
            self._method_not_allowed()

        def do_PUT(self) -> None:  # noqa: N802 (stdlib API)
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802 (stdlib API)
            self._method_not_allowed()

        def _method_not_allowed(self) -> None:
            self._write_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"success": False, "error": "method_not_allowed"},
            )

        def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _WebhookHandler


class WebhookHTTPListener:
    """Threaded HTTP listener wrapping ``ThreadingHTTPServer``."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        route_path: str,
        runner: WebhookDispatchTarget,
        logger: logging.Logger | None = None,
    ) -> None:
        self._log = logger or _LOG
        self._route_path = route_path or WEBHOOK_LISTENER_DEFAULT_PATH
        handler_cls = _build_handler_class(
            route_path=self._route_path,
            runner=runner,
            log=self._log,
        )
        self._server = ThreadingHTTPServer((host, int(port)), handler_cls)
        self._thread: threading.Thread | None = None

    @property
    def bound_host(self) -> str:
        return self._server.server_address[0]

    @property
    def bound_port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def route_path(self) -> str:
        return self._route_path

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="telegram-webhook-listener",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        self._log.info(
            "telegram webhook listener bound host=%s port=%s route=%s",
            self.bound_host,
            self.bound_port,
            self._route_path,
        )

    def stop(self, timeout: float = 5.0) -> None:
        try:
            self._server.shutdown()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("webhook listener shutdown raised: %s", exc)
        try:
            self._server.server_close()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("webhook listener server_close raised: %s", exc)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                self._log.warning(
                    "telegram webhook listener thread did not join within %ss",
                    timeout,
                )
            self._thread = None


def build_listener(
    *,
    config: Any,
    runner: WebhookDispatchTarget,
    logger: logging.Logger | None = None,
) -> WebhookHTTPListener | None:
    """Build listener helper."""
    webhook_cfg = getattr(config, "webhook", None)
    if webhook_cfg is None:
        return None
    bind_port = int(getattr(webhook_cfg, "bind_port", 0) or 0)
    if bind_port <= 0:
        return None
    if not bool(getattr(webhook_cfg, "enabled", False)):
        return None
    return WebhookHTTPListener(
        host=str(getattr(webhook_cfg, "bind_host", "127.0.0.1") or "127.0.0.1"),
        port=bind_port,
        route_path=_resolve_route_path(getattr(webhook_cfg, "url", None)),
        runner=runner,
        logger=logger,
    )


__all__ = [
    "WebhookHTTPListener",
    "WebhookDispatchTarget",
    "build_listener",
]
