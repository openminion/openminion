"""Small Slack Web API wrapper for the controlplane channel."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from openminion.modules.controlplane.interfaces import CONTROLPLANE_INTERFACE_VERSION

HttpPost = Callable[[str, bytes, int], dict[str, Any]]


@dataclass
class SlackAPIError(RuntimeError):
    message: str
    error_code: str = "slack_api_error"
    retryable: bool = False
    retry_after_seconds: float | None = None
    status_code: int | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message)


class SlackTransportError(RuntimeError):
    pass


class SlackWebAPI:
    contract_version = CONTROLPLANE_INTERFACE_VERSION

    def __init__(
        self,
        bot_token: str,
        *,
        http_post: HttpPost | None = None,
        request_timeout_seconds: int = 30,
    ) -> None:
        self._bot_token = str(bot_token or "").strip()
        self._http_post = http_post or self._default_http_post
        self._request_timeout_seconds = int(request_timeout_seconds)

    def auth_test(self) -> dict[str, Any]:
        return self.call("auth.test", {})

    def chat_post_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("chat.postMessage", payload)

    def chat_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("chat.update", payload)

    def apps_connections_open(self) -> dict[str, Any]:
        return self.call("apps.connections.open", {})

    def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._bot_token:
            raise SlackAPIError("missing Slack bot token", error_code="missing_token")
        url = f"https://slack.com/api/{method}"
        body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
        response = self._http_post(url, body, self._request_timeout_seconds)
        if not bool(response.get("ok")):
            code = str(response.get("error") or "slack_api_error")
            raise SlackAPIError(
                f"Slack API {method} failed: {code}",
                error_code=code,
                retryable=code in {"ratelimited", "internal_error", "service_unavailable"},
            )
        return response

    def redacted_token(self) -> str:
        return "[redacted]" if self._bot_token else ""

    def _default_http_post(
        self, url: str, body: bytes, timeout_seconds: int
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After")
                raise SlackAPIError(
                    "Slack API rate limited",
                    error_code="ratelimited",
                    retryable=True,
                    retry_after_seconds=float(retry_after or 1),
                    status_code=429,
                ) from exc
            raise SlackTransportError(f"Slack transport error: HTTP {exc.code}") from exc
        except OSError as exc:
            raise SlackTransportError(str(exc)) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SlackTransportError("Slack returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SlackTransportError("Slack returned non-object JSON")
        return payload
