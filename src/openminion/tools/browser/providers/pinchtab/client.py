import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .constants import (
    DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS,
    DEFAULT_PINCHTAB_BASE_URL,
)


class PinchTabClientError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, body: str = "") -> None:
        super().__init__(message)
        self.status = int(status)
        self.body = str(body)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    backoff_ms: int = 250


@dataclass(frozen=True)
class PinchTabClientConfig:
    base_url: str = DEFAULT_PINCHTAB_BASE_URL
    token: str | None = None
    timeout_s: int = DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS


class PinchTabClient:
    def __init__(
        self,
        config: PinchTabClientConfig | None = None,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout_seconds: int | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if config is None:
            config = PinchTabClientConfig(
                base_url=str(base_url or DEFAULT_PINCHTAB_BASE_URL),
                token=token,
                timeout_s=max(
                    1, int(timeout_seconds or DEFAULT_PINCHTAB_API_TIMEOUT_SECONDS)
                ),
            )
        self.config = config
        self.retry_policy = retry_policy or RetryPolicy()
        self.base_url = self.config.base_url.rstrip("/")

    def _headers(self, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
            headers["X-Bridge-Token"] = self.config.token
        if extra:
            headers.update({str(k): str(v) for k, v in extra.items()})
        return headers

    def _sleep_backoff(self, attempt: int) -> None:
        millis = max(0, int(self.retry_policy.backoff_ms)) * max(1, int(attempt))
        time.sleep(millis / 1000.0)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Mapping[str, Any]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
        expect: str = "json",
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            encoded = urlencode({k: v for k, v in query.items() if v is not None})
            if encoded:
                url = f"{url}?{encoded}"

        payload = None
        headers = self._headers()
        if json_body is not None:
            payload = json.dumps(dict(json_body), ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempts = max(1, int(self.retry_policy.max_retries))
        for attempt in range(1, attempts + 1):
            req = Request(url, data=payload, headers=headers, method=method.upper())
            try:
                with urlopen(
                    req, timeout=max(1, int(self.config.timeout_s))
                ) as response:
                    raw = response.read()
                    if expect == "bytes":
                        return raw
                    if not raw:
                        return {}
                    text = raw.decode("utf-8", errors="replace")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"text": text}
            except HTTPError as exc:
                status = int(getattr(exc, "code", 0) or 0)
                body = ""
                try:
                    body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                retriable = status >= 500
                if retriable and attempt < attempts:
                    self._sleep_backoff(attempt)
                    continue
                raise PinchTabClientError(
                    f"PinchTab HTTP error {status} for {method.upper()} {path}",
                    status=status,
                    body=body,
                ) from exc
            except (URLError, OSError, TimeoutError) as exc:
                if attempt < attempts:
                    self._sleep_backoff(attempt)
                    continue
                raise PinchTabClientError(f"PinchTab request failed: {exc}") from exc

        raise PinchTabClientError("PinchTab request failed after retries")

    @staticmethod
    def _is_missing_route(exc: PinchTabClientError) -> bool:
        return int(getattr(exc, "status", 0)) in {404, 405, 501}

    def _try_post_json(
        self, candidates: list[tuple[str, Mapping[str, Any] | None]]
    ) -> Dict[str, Any]:
        if not candidates:
            raise PinchTabClientError("No PinchTab endpoint candidates were provided")
        last_error: PinchTabClientError | None = None
        for path, body in candidates:
            try:
                payload = self._request("POST", path, json_body=body, expect="json")
                return payload if isinstance(payload, dict) else {"result": payload}
            except PinchTabClientError as exc:
                last_error = exc
                if self._is_missing_route(exc):
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise PinchTabClientError("No PinchTab endpoint candidates were provided")

    def _try_get_json(
        self, candidates: list[tuple[str, Mapping[str, Any] | None]]
    ) -> Dict[str, Any]:
        if not candidates:
            raise PinchTabClientError("No PinchTab endpoint candidates were provided")
        last_error: PinchTabClientError | None = None
        for path, query in candidates:
            try:
                payload = self._request("GET", path, query=query, expect="json")
                return payload if isinstance(payload, dict) else {"result": payload}
            except PinchTabClientError as exc:
                last_error = exc
                if self._is_missing_route(exc):
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise PinchTabClientError("No PinchTab endpoint candidates were provided")

    def _try_get_bytes(
        self, candidates: list[tuple[str, Mapping[str, Any] | None]]
    ) -> bytes:
        if not candidates:
            raise PinchTabClientError("No PinchTab endpoint candidates were provided")
        last_error: PinchTabClientError | None = None
        for path, query in candidates:
            try:
                payload = self._request("GET", path, query=query, expect="bytes")
                return (
                    payload
                    if isinstance(payload, bytes)
                    else bytes(str(payload), "utf-8")
                )
            except PinchTabClientError as exc:
                last_error = exc
                if self._is_missing_route(exc):
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise PinchTabClientError("No PinchTab endpoint candidates were provided")

    def health(self) -> Dict[str, Any]:
        return self._try_get_json([("/health", None)])

    def instance_start(
        self,
        profile_id: str | None = None,
        mode: str | None = None,
        port: int | None = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if profile_id:
            body["profileId"] = profile_id
            body["profile_id"] = profile_id
        if mode:
            body["mode"] = mode
        if port is not None:
            body["port"] = int(port)
        payload = self._try_post_json([("/instances/start", body)])
        instance_id = (
            payload.get("instanceId") or payload.get("instance_id") or payload.get("id")
        )
        if instance_id and "instance_id" not in payload:
            payload["instance_id"] = instance_id
        return payload

    def instance_list(self) -> Dict[str, Any]:
        return self._try_get_json([("/instances", None)])

    def instance_stop(self, instance_id: str) -> Dict[str, Any]:
        payload = self._try_post_json(
            [
                (f"/instances/{instance_id}/stop", None),
                (
                    "/instances/stop",
                    {"instance_id": instance_id, "instanceId": instance_id},
                ),
            ]
        )
        if "stopped" not in payload:
            payload["stopped"] = True
        return payload

    def instance_kill(self, instance_id: str) -> Dict[str, Any]:
        return self._try_post_json([(f"/instances/{instance_id}/kill", None)])

    def tab_new(self, instance_id: str, url: str | None = None) -> Dict[str, Any]:
        payload = self._try_post_json(
            [
                (
                    "/tabs/new",
                    {"instanceId": instance_id, "instance_id": instance_id, "url": url},
                ),
                (
                    "/tabs/open",
                    {"instanceId": instance_id, "instance_id": instance_id, "url": url},
                ),
                (
                    "/navigate",
                    {"instanceId": instance_id, "instance_id": instance_id, "url": url},
                ),
            ]
        )
        tab_id = payload.get("tabId") or payload.get("tab_id") or payload.get("id")
        if tab_id and "tab_id" not in payload:
            payload["tab_id"] = tab_id
        return payload

    def tab_list(self, instance_id: str | None = None) -> Dict[str, Any]:
        if instance_id:
            return self._try_get_json(
                [("/tabs", {"instanceId": instance_id, "instance_id": instance_id})]
            )
        return self._try_get_json([("/tabs", None)])

    def tab_close(self, tab_id: str) -> Dict[str, Any]:
        return self._try_post_json(
            [
                (f"/tabs/{tab_id}/close", None),
                ("/tabs/close", {"tabId": tab_id, "tab_id": tab_id}),
            ]
        )

    def navigate(self, tab_id: str, url: str) -> Dict[str, Any]:
        return self._try_post_json(
            [
                (f"/tabs/{tab_id}/navigate", {"url": url}),
                ("/navigate", {"tabId": tab_id, "tab_id": tab_id, "url": url}),
            ]
        )

    def snapshot(
        self,
        tab_id: str,
        interactive: bool = True,
        compact: bool = True,
        depth: int | None = None,
        max_tokens: int | None = None,
    ) -> Dict[str, Any]:
        query = {
            "interactive": "true" if interactive else "false",
            "compact": "true" if compact else "false",
            "depth": depth,
            "maxTokens": max_tokens,
            "tab_id": tab_id,
            "tabId": tab_id,
        }
        return self._try_get_json(
            [
                (f"/tabs/{tab_id}/snapshot", query),
                ("/snapshot", query),
            ]
        )

    def text(self, tab_id: str, mode: str = "readability") -> Dict[str, Any]:
        return self._try_get_json(
            [
                (f"/tabs/{tab_id}/text", {"mode": mode}),
                ("/text", {"tab_id": tab_id, "tabId": tab_id, "mode": mode}),
            ]
        )

    def screenshot(self, tab_id: str) -> bytes:
        return self._try_get_bytes(
            [
                (f"/tabs/{tab_id}/screenshot", None),
                ("/screenshot", {"tab_id": tab_id, "tabId": tab_id}),
            ]
        )

    def pdf(self, tab_id: str) -> bytes:
        return self._try_get_bytes(
            [
                (f"/tabs/{tab_id}/pdf", None),
                ("/pdf", {"tab_id": tab_id, "tabId": tab_id}),
            ]
        )

    def action(self, tab_id: str, action: Mapping[str, Any]) -> Dict[str, Any]:
        payload = dict(action)
        return self._try_post_json(
            [
                (f"/tabs/{tab_id}/action", payload),
                ("/action", {"tab_id": tab_id, "tabId": tab_id, **payload}),
            ]
        )

    def eval(self, tab_id: str, js: str) -> Dict[str, Any]:
        payload = {"js": js, "script": js, "tabId": tab_id, "tab_id": tab_id}
        return self._try_post_json(
            [
                (f"/tabs/{tab_id}/evaluate", payload),
                (f"/tabs/{tab_id}/eval", payload),
                ("/eval", payload),
            ]
        )

    def lock(self, tab_id: str) -> Dict[str, Any]:
        return self._try_post_json([(f"/tabs/{tab_id}/lock", None)])

    def unlock(self, tab_id: str) -> Dict[str, Any]:
        return self._try_post_json([(f"/tabs/{tab_id}/unlock", None)])


def parse_base_url_targets(base_url: str) -> tuple[str, int, str]:
    parsed = urlparse(str(base_url))
    host = str(parsed.hostname or "")
    scheme = str(parsed.scheme or "http").lower()
    default_port = 443 if scheme == "https" else 80
    port = int(parsed.port) if parsed.port is not None else default_port
    return host, port, scheme
