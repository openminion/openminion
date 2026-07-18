from __future__ import annotations

import json
import socket
import time
import unittest
from http.client import HTTPConnection
from typing import Any

from openminion.modules.controlplane.channels.telegram.listener import (
    WebhookHTTPListener,
)


class _RecordingRunner:
    def __init__(self, *, expected_secret: str) -> None:
        self._expected_secret = expected_secret
        self.calls: list[dict[str, Any]] = []

    def handle_webhook_update(
        self,
        update: dict[str, Any],
        secret_token: object | None = None,
    ) -> dict[str, Any]:
        self.calls.append({"update": update, "secret_token": secret_token})
        token = secret_token if isinstance(secret_token, str) else None
        if token is None:
            return {
                "success": False,
                "error": "unauthorized",
                "reason": "missing_secret_token",
            }
        if token != self._expected_secret:
            return {
                "success": False,
                "error": "unauthorized",
                "reason": "invalid_secret_token",
            }
        update_id = update.get("update_id")
        return {"success": True, "update_id": update_id}


class WebhookHTTPListenerRealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "wh-test-secret"
        self.runner = _RecordingRunner(expected_secret=self.secret)
        self.listener = WebhookHTTPListener(
            host="127.0.0.1",
            port=0,
            route_path="/telegram/webhook",
            runner=self.runner,
        )
        self.listener.start()
        # Wait for the listener thread before sending requests.
        deadline = time.time() + 1.0
        while time.time() < deadline:
            try:
                with socket.create_connection(
                    ("127.0.0.1", self.listener.bound_port), timeout=0.1
                ):
                    break
            except OSError:
                time.sleep(0.01)

    def tearDown(self) -> None:
        self.listener.stop(timeout=2.0)

    def _post(
        self,
        path: str,
        body: dict[str, Any] | None,
        *,
        secret: str | None,
        method: str = "POST",
    ) -> tuple[int, dict[str, Any]]:
        conn = HTTPConnection("127.0.0.1", self.listener.bound_port, timeout=2.0)
        try:
            headers = {"Content-Type": "application/json"}
            if secret is not None:
                headers["X-Telegram-Bot-Api-Secret-Token"] = secret
            payload = json.dumps(body).encode("utf-8") if body is not None else b""
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            try:
                decoded: dict[str, Any] = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError:
                decoded = {"raw": raw.decode("utf-8", errors="replace")}
            return resp.status, decoded
        finally:
            conn.close()

    def test_listener_bound_on_assigned_port(self) -> None:
        self.assertGreater(self.listener.bound_port, 0)
        self.assertEqual(self.listener.bound_host, "127.0.0.1")
        self.assertEqual(self.listener.route_path, "/telegram/webhook")

    def test_happy_path_reaches_runner_and_returns_200(self) -> None:
        status, body = self._post(
            "/telegram/webhook",
            {"update_id": 42, "message": {"message_id": 1}},
            secret=self.secret,
        )
        self.assertEqual(status, 200)
        self.assertTrue(body.get("success"), body)
        self.assertEqual(body.get("update_id"), 42)
        self.assertEqual(len(self.runner.calls), 1)
        call = self.runner.calls[0]
        self.assertEqual(call["update"]["update_id"], 42)
        self.assertEqual(call["secret_token"], self.secret)

    def test_invalid_secret_returns_401(self) -> None:
        status, body = self._post(
            "/telegram/webhook",
            {"update_id": 43},
            secret="wrong-secret",
        )
        self.assertEqual(status, 401)
        self.assertFalse(body.get("success"))
        self.assertEqual(body.get("reason"), "invalid_secret_token")
        # Runner was still invoked once (it owns secret comparison).
        self.assertEqual(len(self.runner.calls), 1)

    def test_missing_secret_returns_401(self) -> None:
        status, body = self._post(
            "/telegram/webhook",
            {"update_id": 44},
            secret=None,
        )
        self.assertEqual(status, 401)
        self.assertFalse(body.get("success"))
        self.assertEqual(body.get("reason"), "missing_secret_token")

    def test_unknown_path_returns_404(self) -> None:
        status, body = self._post(
            "/not-the-webhook",
            {"update_id": 45},
            secret=self.secret,
        )
        self.assertEqual(status, 404)
        self.assertFalse(body.get("success"))
        # Runner was NOT invoked.
        self.assertEqual(len(self.runner.calls), 0)

    def test_get_method_returns_405(self) -> None:
        status, _body = self._post(
            "/telegram/webhook",
            None,
            secret=self.secret,
            method="GET",
        )
        self.assertEqual(status, 405)
        self.assertEqual(len(self.runner.calls), 0)

    def test_malformed_json_returns_400(self) -> None:
        conn = HTTPConnection("127.0.0.1", self.listener.bound_port, timeout=2.0)
        try:
            conn.request(
                "POST",
                "/telegram/webhook",
                body=b"{not-json",
                headers={
                    "Content-Type": "application/json",
                    "X-Telegram-Bot-Api-Secret-Token": self.secret,
                },
            )
            resp = conn.getresponse()
            raw = resp.read()
            self.assertEqual(resp.status, 400)
            body = json.loads(raw.decode("utf-8"))
            self.assertEqual(body.get("error"), "invalid_json")
        finally:
            conn.close()
        self.assertEqual(len(self.runner.calls), 0)


class WebhookListenerPortContentionTests(unittest.TestCase):
    def test_distinct_ports(self) -> None:
        runner_a = _RecordingRunner(expected_secret="a")
        runner_b = _RecordingRunner(expected_secret="b")
        listener_a = WebhookHTTPListener(
            host="127.0.0.1",
            port=0,
            route_path="/telegram/webhook",
            runner=runner_a,
        )
        listener_b = WebhookHTTPListener(
            host="127.0.0.1",
            port=0,
            route_path="/telegram/webhook",
            runner=runner_b,
        )
        listener_a.start()
        listener_b.start()
        try:
            self.assertNotEqual(listener_a.bound_port, listener_b.bound_port)
        finally:
            listener_a.stop(timeout=2.0)
            listener_b.stop(timeout=2.0)
